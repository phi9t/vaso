"""Pure admission checks; no probing, hashing, build or settings mutation."""
def validate_evidence(profile, evidence):
    errors = []
    def require(ok, message):
        if not ok:
            errors.append(message)
    require(evidence.get('torch_commit') == profile['torch']['commit'], 'wrong Torch source commit')
    require(evidence.get('python_abi') == profile['python_abi'], 'Python ABI mismatch')
    require(evidence.get('cuda_toolkit') == profile['cuda_toolkit'], 'CUDA toolkit mismatch')
    require(evidence.get('cuda_arch_list') == profile['cuda_arch_list'], 'SM90 build required')
    require(evidence.get('cupti_api', 0) >= profile['cupti']['minimum_api'], 'CUPTI 13.3 or newer required')
    require(evidence.get('cupti_build_runtime_match') is True, 'CUPTI build/runtime identity unverified')
    require(evidence.get('native_cuspy') is True, 'native Cuspy extension missing')
    require(evidence.get('generated_cupti_stubs') is True, 'generated CUPTI stubs missing')
    require(evidence.get('cupti_python_import') is True, 'CUPTI Python bindings missing')
    require(evidence.get('numpy_import') is True, 'NumPy missing')
    require(evidence.get('nccl_header_closure') is True, 'NCCL nested developer headers incomplete')
    require(evidence.get('driver_runtime_compatible') is True, 'driver compatibility unverified')
    require(evidence.get('cuspy_gpu_trace_passed') is True, 'actual Cuspy GPU trace not qualified')
    require(evidence.get('cuda_event_trace_passed') is True, 'CUDA event tracing not qualified')
    return errors
