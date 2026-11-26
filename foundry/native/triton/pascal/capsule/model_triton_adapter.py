"""Pascal FP32 inference adapter with dense and native packed expert paths."""
from collections import Counter
import math
import torch
import torch.nn.functional as F
import triton
import triton.language as tl
from inkling_primitives import tanh, route, combine
from transformers.models.inkling2_loop import modeling_inkling2_loop as model

calls=Counter()

@triton.jit
def rms_kernel(X,W,Y,H:tl.constexpr,B:tl.constexpr,EPS:tl.constexpr,WEIGHTED:tl.constexpr):
    r=tl.program_id(0);i=tl.arange(0,B);m=i<H
    x=tl.load(X+r*H+i,m,0).to(tl.float32)
    y=x*tl.rsqrt(tl.sum(x*x,0)/H+EPS)
    if WEIGHTED:
        y*=tl.load(W+i,m,0).to(tl.float32)
    tl.store(Y+r*H+i,y,m)

@triton.jit
def softcap_kernel(X,Y,N:tl.constexpr,C:tl.constexpr,B:tl.constexpr):
    i=tl.program_id(0)*B+tl.arange(0,B)
    x=tl.load(X+i,i<N,0).to(tl.float32)
    tl.store(Y+i,C*tanh(x/C),i<N)

@triton.jit
def swiglu_kernel(G,U,Y,N:tl.constexpr,GC:tl.constexpr,UC:tl.constexpr,B:tl.constexpr):
    i=tl.program_id(0)*B+tl.arange(0,B);m=i<N
    g=tl.load(G+i,m,0).to(tl.float32);u=tl.load(U+i,m,0).to(tl.float32)
    tl.store(Y+i,GC*tanh(g/GC)*tl.sigmoid(g)*UC*tanh(u/UC),m)

@triton.jit
def gate_kernel(X,M,F,G,Y,N:tl.constexpr,B:tl.constexpr):
    i=tl.program_id(0)*B+tl.arange(0,B);m=i<N
    x=tl.load(X+i,m,0);msg=tl.load(M+i,m,0)
    f=tl.load(F+i,m,0);g=tl.load(G+i,m,0)
    tl.store(Y+i,(1.-tanh(f))*x+tanh(g)*msg,m)

@triton.jit
def conv_kernel(X,W,Y,T:tl.constexpr,H:tl.constexpr,K:tl.constexpr,B:tl.constexpr):
    r=tl.program_id(0);c=r%H
    t=tl.program_id(1)*B+tl.arange(0,B)
    acc=tl.zeros((B,),tl.float32)
    for lag in tl.static_range(K):
        x=tl.load(X+r*T+t-lag,(t<T)&(t>=lag),0).to(tl.float32)
        w=tl.load(W+c*K+K-1-lag).to(tl.float32)
        acc+=x*w
    tl.store(Y+r*T+t,acc,t<T)

@triton.jit
def dynconv_kernel(X,STATIC,DYN,RES,Y,T:tl.constexpr,H:tl.constexpr,HEADS:tl.constexpr,K:tl.constexpr,B:tl.constexpr):
    r=tl.program_id(0);batch=r//T;t=r%T
    c=tl.program_id(1)*B+tl.arange(0,B);valid=c<H
    hd=c//(H//HEADS);acc=tl.zeros((B,),tl.float32)
    for lag in tl.static_range(K):
        v=tl.load(X+(batch*H+c)*(T+K-1)+t+K-1-lag,valid,0)
        w=tl.load(STATIC+lag*H+c,valid,0)+tl.load(DYN+(r*HEADS+hd)*K+lag,valid,0)
        acc+=v*w
    residual=tl.load(RES+r*H+c,valid,0)
    tl.store(Y+r*H+c,(acc+residual)/tl.sqrt(tl.full((),2*K+1,tl.float32)),valid)

@triton.jit
def expert_linear(X,W,IDX,Y,I:tl.constexpr,O:tl.constexpr,K:tl.constexpr,B:tl.constexpr,PER_SLOT:tl.constexpr):
    slot=tl.program_id(0);row=tl.program_id(1);i=tl.arange(0,B)
    e=tl.load(IDX+slot)
    input_row=slot if PER_SLOT else slot//K
    x=tl.load(X+input_row*I+i,i<I,0).to(tl.float32)
    w=tl.load(W+(e*O+row)*I+i,i<I,0).to(tl.float32)
    tl.store(Y+slot*O+row,tl.sum(x*w,0))

@triton.jit
def attn_kernel(Q,K,V,BIAS,SINK,OUT,PROB,H:tl.constexpr,KV:tl.constexpr,QN:tl.constexpr,KN:tl.constexpr,D:tl.constexpr,BN:tl.constexpr,BD:tl.constexpr,SCALE:tl.constexpr,HAS_SINK:tl.constexpr):
    row=tl.program_id(0);qp=row%QN;h=(row//QN)%H;b=row//(H*QN)
    kv=b*KV+h//(H//KV)
    n=tl.arange(0,BN);d=tl.arange(0,BD)
    q=tl.load(Q+row*D+d,d<D,0).to(tl.float32)
    k=tl.load(K+(kv*KN+n[:,None])*D+d[None,:],(n[:,None]<KN)&(d[None,:]<D),0).to(tl.float32)
    z=tl.sum(k*q[None,:],1)*SCALE+tl.load(BIAS+row*KN+n,n<KN,0)
    z=tl.where(n<KN,z,float("-inf"))
    mx=tl.max(z,0)
    if HAS_SINK:
        s=tl.load(SINK+row)
        mx=tl.maximum(mx,s)
        sink_weight=tl.exp(s-mx)
    else:
        sink_weight=0.0
    w=tl.exp(z-mx);w=w/(tl.sum(w,0)+sink_weight)
    tl.store(PROB+row*KN+n,w,n<KN)
    v=tl.load(V+(kv*KN+n[:,None])*D+d[None,:],(n[:,None]<KN)&(d[None,:]<D),0).to(tl.float32)
    tl.store(OUT+row*D+d,tl.sum(v*w[:,None],0),d<D)

def launch(name,fn,grid,*args,**kwargs):
    k=fn[grid](*args,**kwargs)
    assert ".target sm_61" in k.asm["ptx"],name
    calls[name]+=1
    return k

def rms(self,x):
    a=x.contiguous();h=a.shape[-1];out=torch.empty_like(a)
    w=getattr(self,"weight",None)
    launch("rmsnorm",rms_kernel,(a.numel()//h,),a,w.contiguous() if w is not None else a,out,h,triton.next_power_of_2(h),self.variance_epsilon,w is not None,num_warps=4)
    return out

def softcap(self,x):
    a=x.contiguous();out=torch.empty_like(a)
    launch("softcap",softcap_kernel,(triton.cdiv(a.numel(),256),),a,out,a.numel(),self.softcap,256)
    return out

def swiglu(self,g,u):
    g,u=g.contiguous(),u.contiguous();out=torch.empty_like(g)
    launch("swiglu",swiglu_kernel,(triton.cdiv(g.numel(),256),),g,u,out,g.numel(),self.gate_softcap.softcap,self.up_softcap.softcap,256)
    return out

def message(self,x,m):
    x,m=x.float().contiguous(),m.float().contiguous()
    z=self.down_proj(torch.cat([self.norm(x),self.norm(m)],-1).to(self.down_proj.weight.dtype)).float()
    f=self.forget_gate_proj(z).contiguous();g=self.message_gate_proj(z).contiguous()
    out=torch.empty_like(x)
    launch("message_mix",gate_kernel,(triton.cdiv(x.numel(),256),),x,m,f,g,out,x.numel(),256)
    return out

def conv_fn(x,w,bias=None,activation=None,**kw):
    assert bias is None and activation is None and kw.get("seq_idx") is None
    x=x.to(w.dtype).contiguous();w=w.contiguous();b,h,t=x.shape;out=torch.empty_like(x)
    launch("conv_prefill",conv_kernel,(b*h,triton.cdiv(t,32)),x,w,out,t,h,w.shape[-1],32)
    return out

def conv_update(x,state,w,bias=None,activation=None):
    assert bias is None and activation is None
    n=x.shape[-1];joined=torch.cat([state,x],-1).to(w.dtype)
    state.copy_(joined[:,:,-state.shape[-1]:])
    out=conv_fn(joined,w)[:,:,-n:].to(x.dtype)
    calls["conv_cache_update"]+=1
    return out

def dynamic_conv(self,x,dynamic_input,cache=None,conv_mask=None,cache_offset=0,**kw):
    assert kw.get("seq_idx") is None
    dtype=x.dtype;b,t,h=x.shape;k=self.conv_kernel_size;heads=self.num_heads
    weights=self.weight_proj(dynamic_input).float().contiguous()
    residual=x.float().contiguous()
    context=model.apply_mask_to_padding_states(residual,conv_mask).transpose(1,2)
    if cache is not None:
        context=cache.update_conv_state(context,self.layer_idx+cache_offset,state_idx=self.conv_idx,conv_kernel_size=k)
    context=F.pad(context,(k-1,0))[...,-(t+k-1):].contiguous()
    out=torch.empty_like(residual)
    launch("dynamic_conv",dynconv_kernel,(b*t,triton.cdiv(h,128)),context,self.static_weight.float().contiguous(),weights,residual,out,t,h,heads,k,128)
    return out.to(dtype)

def router(self,x):
    flat=x.float().reshape(-1,self.hidden_dim)
    logits=F.linear(flat,self.weight).contiguous()
    assert self.routed_scaling_factor==8.0
    idx=torch.empty((flat.shape[0],self.top_k),dtype=torch.int32,device=x.device)
    weights=torch.empty(idx.shape,dtype=torch.float32,device=x.device)
    launch("topk_route",route,(flat.shape[0],),logits,self.e_score_correction_bias.contiguous(),idx,weights,self.num_experts,self.top_k)
    return logits,weights,idx.long()


def _packed_linear(self, projection, x, expert_ids, counter_name):
    """Group active rows and admit only the selected packed expert slices."""
    from packed_experts import packed_expert_linear
    if x.dtype != torch.float32 or x.device.type != "cuda":
        raise TypeError("Packed expert kernels require CUDA FP32 activations")
    x, expert_ids = x.contiguous(), expert_ids.contiguous()
    matrix = self.packed_weights.matrices[(self.packed_layer, projection)]
    if expert_ids.shape != x.shape[:-1]:
        raise ValueError("Packed expert IDs must match activation rows")
    out = torch.empty((x.shape[0], matrix.output_features), dtype=torch.float32, device=x.device)
    for expert in torch.unique(expert_ids, sorted=True).tolist():
        positions = torch.where(expert_ids == expert)[0]
        selected = x.index_select(0, positions)
        entry = self.packed_weights.get(self.packed_layer, projection, expert, x.device)
        local_ids = torch.zeros(positions.shape, dtype=torch.int32, device=x.device)
        values = packed_expert_linear(
            selected, local_ids, entry.weight, entry.scales,
            format=matrix.format, outer_scale=entry.outer_scale,
        )
        out.index_copy_(0, positions, values)
        calls[counter_name] += 1
        calls["packed_" + matrix.format + "_" + projection] += 1
        calls["packed_" + matrix.format] += 1
        # Do not retain an evicted expert in a loop-local variable on the next admission.
        del entry, selected, local_ids, values
    return out

def _packed_experts(self, x, idx, weights):
    if x.dtype != torch.float32:
        raise TypeError("Packed mode preserves FP32 activations")
    x, idx, weights = x.contiguous(), idx.contiguous(), weights.float().contiguous()
    t, h = x.shape
    k = idx.shape[1]
    flat_ids = idx.reshape(-1)
    inputs = x[:, None, :].expand(t, k, h).reshape(t * k, h)
    raw = _packed_linear(self, "w13", inputs, flat_ids, "expert_gate_up")
    # Stored w13 rows are [gate0,up0,gate1,up1,...]. The original
    # Transformers converter deinterleaves dense weights; keep packed bytes intact.
    gate, up = raw[:, 0::2], raw[:, 1::2]
    activated = swiglu(self.act_fn, gate, up)
    down = _packed_linear(self, "w2", activated, flat_ids, "expert_down").view(t, k, h)
    out = torch.empty_like(x)
    launch("expert_accum", combine, (t, triton.cdiv(h, 128)),
           down, weights, out, h, k, 128)
    return out

def experts(self,x,idx,w):
    if getattr(self,"is_packed",False):
        return _packed_experts(self,x,idx,w)
    x=x.contiguous();idx=idx.contiguous();w=w.float().contiguous()
    t,h=x.shape;k=idx.shape[1];inter=self.intermediate_dim
    gateup=torch.empty((t*k,2*inter),dtype=x.dtype,device=x.device)
    launch("expert_gate_up",expert_linear,(t*k,2*inter),x,self.gate_up_proj.contiguous(),idx,gateup,h,2*inter,k,triton.next_power_of_2(h),False)
    g,u=gateup.chunk(2,-1);z=swiglu(self.act_fn,g,u)
    down=torch.empty((t,k,h),dtype=x.dtype,device=x.device)
    launch("expert_down",expert_linear,(t*k,h),z,self.down_proj.contiguous(),idx,down,inter,h,k,triton.next_power_of_2(inter),True)
    out=torch.empty_like(x)
    launch("expert_accum",combine,(t,triton.cdiv(h,128)),down,w,out,h,k,128)
    return out

def attention(module,q,k,v,attention_mask,scaling,dropout=0.,position_bias=None,sinks=None,**kw):
    assert dropout==0.0
    q,k,v=q.contiguous(),k.contiguous(),v.contiguous()
    b,h,qn,d=q.shape;kv=k.shape[1];kn=k.shape[2]
    assert h%kv==0 and k.shape[-1]==d and v.shape[-1]==d
    bias=torch.zeros((b,h,qn,kn),dtype=torch.float32,device=q.device)
    if position_bias is not None:bias=bias+position_bias.float()
    if attention_mask is not None:bias=bias+attention_mask.float()
    sink=sinks.contiguous() if sinks is not None else q
    out=torch.empty_like(q);probs=torch.empty((b,h,qn,kn),dtype=q.dtype,device=q.device)
    launch("attention",attn_kernel,(b*h*qn,),q,k,v,bias.contiguous(),sink,out,probs,h,kv,qn,kn,d,triton.next_power_of_2(kn),triton.next_power_of_2(d),scaling,sinks is not None,num_warps=4)
    return out.transpose(1,2).contiguous(),probs

def install():
    model.Inkling2LoopUnweightedRMSNorm.forward=rms
    model.Inkling2LoopRMSNorm.forward=rms
    model.Inkling2LoopSoftcap.forward=softcap
    model.Inkling2LoopSoftcappedSwiGLU.forward=swiglu
    model.Inkling2LoopMessageGate.forward=message
    model.Inkling2LoopDynamicShortConvolution.forward=dynamic_conv
    model.Inkling2LoopTopKRouter.forward=router
    model.Inkling2LoopExperts.forward=experts
    model.causal_conv1d_fn=conv_fn
    model.causal_conv1d_update=conv_update
    model.eager_attention_forward=attention
