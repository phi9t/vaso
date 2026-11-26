"""Shared FP32 routing and accumulation primitives for the Inkling adapter."""
import triton
import triton.language as tl

@triton.jit
def tanh(x):
    return 2.0 * tl.sigmoid(2.0 * x) - 1.0


@triton.jit
def route(LOGITS,BIAS,IDX,W,E:tl.constexpr,K:tl.constexpr):
    r=tl.program_id(0);i=tl.arange(0,E);j=tl.arange(0,K)
    scores=tl.sigmoid(tl.load(LOGITS+r*E+i))
    rank=scores+tl.load(BIAS+i)
    out_i=tl.full((K,),0,tl.int32);out_w=tl.full((K,),0,tl.float32)
    for k in tl.static_range(K):
        idx=tl.argmax(rank,0)
        value=tl.sum(tl.where(i==idx,scores,0.0),0)
        out_i=tl.where(j==k,idx,out_i);out_w=tl.where(j==k,value,out_w)
        rank=tl.where(i==idx,float("-inf"),rank)
    out_w=8.0*out_w/(tl.sum(out_w,0)+1e-20)
    tl.store(IDX+r*K+j,out_i);tl.store(W+r*K+j,out_w)


@triton.jit
def combine(EXP,W,Y,H:tl.constexpr,K:tl.constexpr,B:tl.constexpr):
    t=tl.program_id(0);i=tl.program_id(1)*B+tl.arange(0,B)
    k=tl.arange(0,K)
    w=tl.load(W+t*K+k)
    v=tl.load(EXP+(t*K+k[:,None])*H+i[None,:],i[None,:]<H,0).to(tl.float32)
    tl.store(Y+t*H+i,tl.sum(v*w[:,None],0),i<H)
