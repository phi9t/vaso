"""Acceptance gate for an installed wheel, not proof from configuration strings.
Runs real eager autograd/AdamW, DDP/NCCL, asynchronous stream work, and Kineto.
Every report records the imported torch path and exact version to prevent a
pre-existing installation from being mistaken for the newly built artifact.
"""
from __future__ import annotations
import argparse,datetime,json,os,pickle,time
from pathlib import Path
import torch
import torch.distributed as dist
import torch.multiprocessing as mp
from torch import nn
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.profiler import profile,ProfilerActivity,record_function
from parallel_components import run_parallel_components

def worker(rank:int,world:int,output:str,expected:str,torch_root:str)->None:
    out=Path(output)
    actual=Path(torch.__file__).resolve()
    if not str(actual).startswith(str(Path(torch_root).resolve())+os.sep):
        raise RuntimeError(f'Unexpected torch import: {actual}')
    if str(torch.__version__)!=expected:
        raise RuntimeError(f'Expected {expected}, imported {torch.__version__}')
    torch.set_num_threads(1)
    torch.cuda.set_device(rank)
    if torch.cuda.get_device_capability(rank)!=(6,1):raise RuntimeError('Expected SM61 device')
    if expected.startswith('2.15.') and 'sm_61' not in torch.cuda.get_arch_list():raise RuntimeError('New wheel lacks explicit SM61 code')
    device=torch.device('cuda',rank)
    dist.init_process_group('nccl',init_method='file://'+str(out/'rendezvous'),rank=rank,world_size=world,timeout=datetime.timedelta(seconds=40))
    torch.manual_seed(711)
    reference=nn.Sequential(nn.Linear(32,64),nn.GELU(),nn.Linear(64,16))
    gpu=nn.Sequential(nn.Linear(32,64),nn.GELU(),nn.Linear(64,16)).to(device)
    gpu.load_state_dict(reference.state_dict())
    ddp=DDP(gpu,device_ids=[rank])
    cpuopt=torch.optim.AdamW(reference.parameters(),lr=1e-3,foreach=False)
    gpuopt=torch.optim.AdamW(ddp.parameters(),lr=1e-3,foreach=False)
    checks=[]
    # Identical inputs intentionally make distributed averaged gradients exactly
    # comparable with a single CPU replica, modulo ordinary FP32 rounding.
    for step in range(3):
        torch.manual_seed(800+step);x=torch.randn(3,7,32);target=torch.randn(3,7,16)
        cpuopt.zero_grad();gpuopt.zero_grad()
        cy=reference(x);gy=ddp(x.to(device));cl=(cy-target).square().mean();gl=(gy-target.to(device)).square().mean()
        cl.backward();gl.backward()
        torch.testing.assert_close(gy.detach().cpu(),cy.detach(),rtol=1e-4,atol=1e-5)
        for a,b in zip(reference.parameters(),gpu.parameters()):torch.testing.assert_close(b.grad.cpu(),a.grad,rtol=2e-3,atol=3e-5)
        cpuopt.step();gpuopt.step();checks.append({'step':step,'cpu_loss':float(cl.detach()),'gpu_loss':float(gl.detach())})
    for a,b in zip(reference.parameters(),gpu.parameters()):torch.testing.assert_close(b.detach().cpu(),a.detach(),rtol=2e-3,atol=3e-5)
    # Real collective identity and device work in the captured interval.
    val=torch.tensor([rank+1.0],device=device)
    prof_path=out/f'rank{rank}.kineto.json'
    with profile(activities=[ProfilerActivity.CPU,ProfilerActivity.CUDA],record_shapes=True) as prof:
        with record_function('faro|qualification|nccl'):
            work=dist.all_reduce(val,async_op=True);work.wait()
        with record_function('faro|qualification|forward_backward'):
            gpuopt.zero_grad();loss=ddp(torch.randn(2,16,32,device=device)).square().mean();loss.backward();gpuopt.step()
        with record_function('faro|qualification|stream'):
            stream=torch.cuda.Stream(device=device)
            with torch.cuda.stream(stream):
                a=torch.randn(128,128,device=device);c=a@a
                event=torch.cuda.Event();event.record()
            torch.cuda.current_stream(device).wait_event(event)
            assert bool(torch.isfinite(c).all())
        components = run_parallel_components(rank, device, world)
        torch.cuda.synchronize()
    assert float(val)==world*(world+1)/2
    prof.export_chrome_trace(str(prof_path))
    trace=json.loads(prof_path.read_text())['traceEvents']
    kernels=[e for e in trace if e.get('cat')=='kernel']
    cpu=[e for e in trace if e.get('cat')=='cpu_op']
    if not kernels or not cpu:raise RuntimeError('Profiler captured no CUDA kernels or CPU operators')
    native_fr=out/f'rank{rank}.flight-recorder.bin';fr_error=None
    try:
        # PyTorch 2.15's default NCCL backend is nccl2. It records through
        # the c10d FlightRecorderHook into the backend-scoped generic recorder.
        # _dump_nccl_trace() only dumps the separate nccl-legacy native recorder
        # (empty for nccl2), so never substitute its empty dump for real proof.
        if expected.startswith('2.15.'):
            fr_backend='nccl2'
            dump=torch._C._distributed_c10d._dump_fr_trace(backend=fr_backend)
        else:
            fr_backend='nccl-legacy'
            dump=torch._C._distributed_c10d._dump_nccl_trace()
        if isinstance(dump,(bytes,bytearray)):native_fr.write_bytes(dump)
        else:fr_error='Unexpected FR dump type '+type(dump).__name__
    except Exception as exc:fr_error=str(exc)
    if fr_error is not None or not native_fr.is_file():raise RuntimeError('Native Flight Recorder capture failed: '+str(fr_error))
    fr=pickle.loads(native_fr.read_bytes())  # only data emitted by this process
    fr_entries=len(fr.get('entries',[]))
    if fr_entries==0:raise RuntimeError('Flight Recorder dump contains no collective entries')
    maps=Path('/proc/self/maps').read_text();cupti=sorted({line.split()[-1] for line in maps.splitlines() if 'libcupti.so' in line})
    if any('.so.13' in x for x in cupti):raise RuntimeError('Unexpected CUPTI13 contamination in Pascal qualification')
    result={'rank':rank,'passed':True,'torch_version':str(torch.__version__),'torch_path':str(actual),'cuda_version':torch.version.cuda,
        'arch_list':torch.cuda.get_arch_list(),'device':torch.cuda.get_device_name(rank),'capability':list(torch.cuda.get_device_capability(rank)),
        'numerical_checks':checks,'nccl_allreduce':float(val),'kineto_cuda_kernels':len(kernels),'kineto_cpu_ops':len(cpu),'cupti_libraries':cupti,
        'parallel_components':components,'fr_backend':fr_backend,'fr_entries':fr_entries,'fr_dump_bytes':native_fr.stat().st_size if native_fr.exists() else 0,'fr_dump_error':fr_error}
    (out/f'rank{rank}.result.json').write_text(json.dumps(result,indent=2))
    dist.barrier();dist.destroy_process_group()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--expected',required=True);p.add_argument('--torch-root',required=True);p.add_argument('--control',action='store_true')
    a=p.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=False)
    os.environ['TORCH_FR_BUFFER_SIZE']='4096';os.environ['TORCH_NCCL_TRACE_BUFFER_SIZE']='4096';os.environ['TORCH_NCCL_ENABLE_TIMING']='1'
    if torch.cuda.device_count()<2:raise RuntimeError('Two admitted GPUs required')
    mp.spawn(worker,args=(2,str(out),a.expected,a.torch_root),nprocs=2,join=True)
    ranks=[json.loads((out/f'rank{i}.result.json').read_text()) for i in range(2)]
    result={'passed':all(x['passed'] for x in ranks),'control_only':a.control,'torch_version':a.expected,'ranks':ranks,'new_wheel_qualified':not a.control}
    (out/'summary.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
