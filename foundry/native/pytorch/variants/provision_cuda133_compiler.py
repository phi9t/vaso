from pathlib import Path
import argparse,json,subprocess
parser=argparse.ArgumentParser(description="Stage the NVIDIA CUDA13.3 compiler and CUPTI in an isolated prefix, without system package installation")
parser.add_argument("--root",type=Path,required=True)
parser.add_argument("--package-cache",type=Path)
parser.add_argument("--torch-libraries",action="store_true",help="Also stage CUDA math libraries, NCCL and cuDNN for Torch")
args=parser.parse_args()
P=args.root.resolve();P.mkdir(parents=True,exist_ok=True)
base="https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/"
cache=args.package_cache
names=["cuda-nvcc-13-3","cuda-crt-13-3","libnvvm-13-3","libnvptxcompiler-13-3","cccl-13-3","cuda-cudart-13-3","cuda-cudart-dev-13-3","cuda-culibos-dev-13-3","cuda-driver-dev-13-3","cuda-cupti-13-3","cuda-cupti-dev-13-3","cuda-nvml-dev-13-3"]
if args.torch_libraries:
 names += ["cuda-nvrtc-13-3","cuda-nvrtc-dev-13-3","libnvjitlink-13-3","libnvjitlink-dev-13-3","libcublas-13-3","libcublas-dev-13-3","libcusparse-13-3","libcusparse-dev-13-3","libcusolver-13-3","libcusolver-dev-13-3","libcurand-13-3","libcurand-dev-13-3","libcufft-13-3","libcufft-dev-13-3","libnccl2=2.30.7-1+cuda13.3","libnccl-dev=2.30.7-1+cuda13.3","libcudnn9-cuda-13=9.24.0.43-1","libcudnn9-dev-cuda-13=9.24.0.43-1","libcudnn9-headers-cuda-13=9.24.0.43-1"]
if (P/"prefix").exists():
 raise SystemExit("Refusing existing prefix; use a fresh staging root")
(P/"packages").mkdir(exist_ok=True)
records=[]
for name in names:
 text=subprocess.check_output(["apt-cache","show",name],text=True)
 fields=dict(l.split(": ",1) for l in text.split("\n\n")[0].splitlines() if ": " in l)
 filename=Path(fields["Filename"]).name
 package=cache/filename if cache else P/"packages"/filename
 expected_size=int(fields["Size"])
 if not package.is_file() or package.stat().st_size!=expected_size:
  package=P/"packages"/filename
  if not package.is_file() or package.stat().st_size!=expected_size:
   partial=package.with_suffix(package.suffix+".partial")
   subprocess.run(["curl","-fLsS","--connect-timeout","10","--max-time","60",base+filename,"-o",str(partial)],check=True)
   if partial.stat().st_size!=expected_size:raise RuntimeError("Incomplete download: "+filename)
   partial.replace(package)
 fields["local_package"]=str(package)
 records.append({k:fields[k] for k in ["Package","Version","Filename","Size","local_package"]})
 (P/"packages-used.json").write_text(json.dumps(records,indent=2)+"\n")
 print("DOWNLOADED",name,fields["Version"],flush=True)

(P/"prefix").mkdir()
for item in records:
 subprocess.run(["dpkg-deb","-x",item["local_package"],str(P/"prefix")],check=True)
 print("EXTRACTED",item["Package"],item["Version"],flush=True)
