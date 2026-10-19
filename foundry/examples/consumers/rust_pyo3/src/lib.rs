use memmap2::{Mmap, MmapOptions};
use pyo3::exceptions::{PyKeyError, PyRuntimeError, PyValueError};
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyModule};
use regex::Regex;
use serde::Deserialize;
use serde_json::Value;
use std::collections::HashMap;
use std::ffi::c_char;
use std::fs::File;

#[derive(Clone)]
struct MergeRule {
    left: Vec<u8>,
    right: Vec<u8>,
    merged: Vec<u8>,
    token: u32,
    rank: usize,
}

#[pyclass]
struct FixtureTokenizer {
    pattern: Regex,
    token_to_bytes: HashMap<u32, Vec<u8>>,
    bytes_to_token: HashMap<Vec<u8>, u32>,
    merges: Vec<MergeRule>,
}

fn merge_rules() -> Vec<MergeRule> {
    let pairs: &[(&[u8], &[u8])] = &[
        (b"t", b"h"),
        (b"th", b"e"),
        (b"e", b"r"),
        (b"i", b"n"),
        (b"o", b"n"),
        (b" ", b"t"),
        (b"r", b"u"),
        (b"s", b"t"),
        (b"e", b"n"),
        (b"c", b"e"),
        (b"p", b"y"),
        (b"m", b"m"),
    ];
    pairs
        .iter()
        .enumerate()
        .map(|(rank, (left, right))| {
            let mut merged = Vec::with_capacity(left.len() + right.len());
            merged.extend_from_slice(left);
            merged.extend_from_slice(right);
            MergeRule {
                left: left.to_vec(),
                right: right.to_vec(),
                merged,
                token: 256 + rank as u32,
                rank,
            }
        })
        .collect()
}

impl FixtureTokenizer {
    fn build() -> PyResult<Self> {
        let mut token_to_bytes = HashMap::new();
        let mut bytes_to_token = HashMap::new();
        for byte in 0u16..=255 {
            let bytes = vec![byte as u8];
            token_to_bytes.insert(byte as u32, bytes.clone());
            bytes_to_token.insert(bytes, byte as u32);
        }
        let merges = merge_rules();
        for rule in &merges {
            token_to_bytes.insert(rule.token, rule.merged.clone());
            bytes_to_token.insert(rule.merged.clone(), rule.token);
        }
        Ok(Self {
            pattern: Regex::new(r"\w+|[^\w\s]+|\s+")
                .map_err(|err| PyRuntimeError::new_err(err.to_string()))?,
            token_to_bytes,
            bytes_to_token,
            merges,
        })
    }

    fn encode_piece(&self, bytes: &[u8]) -> PyResult<Vec<u32>> {
        let mut parts: Vec<Vec<u8>> = bytes.iter().map(|byte| vec![*byte]).collect();
        loop {
            let mut best: Option<(usize, usize)> = None;
            for idx in 0..parts.len().saturating_sub(1) {
                for rule in &self.merges {
                    if parts[idx] == rule.left && parts[idx + 1] == rule.right {
                        if best.map_or(true, |(_, rank)| rule.rank < rank) {
                            best = Some((idx, rule.rank));
                        }
                    }
                }
            }
            let Some((idx, rank)) = best else {
                break;
            };
            let rule = self
                .merges
                .iter()
                .find(|rule| rule.rank == rank && parts[idx] == rule.left && parts[idx + 1] == rule.right)
                .ok_or_else(|| PyRuntimeError::new_err("missing selected merge rule"))?;
            parts.splice(idx..idx + 2, [rule.merged.clone()]);
        }
        parts
            .into_iter()
            .map(|part| {
                self.bytes_to_token
                    .get(&part)
                    .copied()
                    .ok_or_else(|| PyRuntimeError::new_err("missing fixture token"))
            })
            .collect()
    }
}

#[pymethods]
impl FixtureTokenizer {
    #[new]
    fn new() -> PyResult<Self> {
        Self::build()
    }

    fn encode(&self, text: &str) -> PyResult<Vec<u32>> {
        let mut out = Vec::new();
        for piece in self.pattern.find_iter(text) {
            out.extend(self.encode_piece(piece.as_str().as_bytes())?);
        }
        Ok(out)
    }

    fn decode(&self, ids: Vec<u32>) -> PyResult<String> {
        let mut bytes = Vec::new();
        for id in ids {
            let token = self
                .token_to_bytes
                .get(&id)
                .ok_or_else(|| PyKeyError::new_err(format!("unknown token id {id}")))?;
            bytes.extend_from_slice(token);
        }
        String::from_utf8(bytes).map_err(|err| PyValueError::new_err(err.to_string()))
    }
}

#[derive(Clone, Debug)]
struct TensorSpec {
    dtype: String,
    shape: Vec<usize>,
    begin: usize,
    end: usize,
}

#[derive(Deserialize)]
struct RawTensorSpec {
    dtype: String,
    shape: Vec<usize>,
    data_offsets: [usize; 2],
}

#[pyclass(unsendable)]
struct TensorFile {
    mmap: Mmap,
    data_start: usize,
    tensors: HashMap<String, TensorSpec>,
}

fn parse_safetensors_header(mmap: &Mmap) -> PyResult<(usize, HashMap<String, TensorSpec>)> {
    if mmap.len() < 8 {
        return Err(PyValueError::new_err("safetensors file is shorter than its header length"));
    }
    let mut len_bytes = [0u8; 8];
    len_bytes.copy_from_slice(&mmap[..8]);
    let header_len = u64::from_le_bytes(len_bytes) as usize;
    let data_start = 8usize
        .checked_add(header_len)
        .ok_or_else(|| PyValueError::new_err("safetensors header length overflow"))?;
    if data_start > mmap.len() {
        return Err(PyValueError::new_err("safetensors header extends past end of file"));
    }
    let header = std::str::from_utf8(&mmap[8..data_start])
        .map_err(|err| PyValueError::new_err(err.to_string()))?;
    let root: HashMap<String, Value> =
        serde_json::from_str(header).map_err(|err| PyValueError::new_err(err.to_string()))?;
    let mut tensors = HashMap::new();
    for (name, value) in root {
        if name == "__metadata__" {
            continue;
        }
        let raw: RawTensorSpec =
            serde_json::from_value(value).map_err(|err| PyValueError::new_err(err.to_string()))?;
        if raw.data_offsets[0] > raw.data_offsets[1] {
            return Err(PyValueError::new_err(format!("{name}: invalid data_offsets")));
        }
        let end = data_start
            .checked_add(raw.data_offsets[1])
            .ok_or_else(|| PyValueError::new_err(format!("{name}: data offset overflow")))?;
        if end > mmap.len() {
            return Err(PyValueError::new_err(format!("{name}: tensor data extends past file")));
        }
        tensors.insert(
            name,
            TensorSpec {
                dtype: raw.dtype,
                shape: raw.shape,
                begin: raw.data_offsets[0],
                end: raw.data_offsets[1],
            },
        );
    }
    Ok((data_start, tensors))
}

fn torch_dtype<'py>(torch: &Bound<'py, PyModule>, dtype: &str) -> PyResult<Bound<'py, PyAny>> {
    let attr = match dtype {
        "F32" => "float32",
        "F64" => "float64",
        "I64" => "int64",
        "I32" => "int32",
        "U8" => "uint8",
        other => {
            return Err(PyValueError::new_err(format!(
                "unsupported safetensors dtype {other}"
            )))
        }
    };
    torch.getattr(attr)
}

#[pymethods]
impl TensorFile {
    fn names(&self) -> Vec<String> {
        let mut names: Vec<String> = self.tensors.keys().cloned().collect();
        names.sort();
        names
    }

    fn mmap_bounds(&self) -> (usize, usize) {
        let start = self.mmap.as_ptr() as usize;
        (start, start + self.mmap.len())
    }

    fn tensor(&self, py: Python<'_>, name: &str) -> PyResult<PyObject> {
        let spec = self
            .tensors
            .get(name)
            .ok_or_else(|| PyKeyError::new_err(format!("unknown tensor {name}")))?;
        let abs_begin = self.data_start + spec.begin;
        let byte_len = spec.end - spec.begin;
        let ptr = unsafe { self.mmap.as_ptr().add(abs_begin) as *mut c_char };
        let memory = unsafe {
            let obj = ffi::PyMemoryView_FromMemory(ptr, byte_len as ffi::Py_ssize_t, ffi::PyBUF_READ);
            if obj.is_null() {
                return Err(PyRuntimeError::new_err("PyMemoryView_FromMemory failed"));
            }
            PyObject::from_owned_ptr(py, obj)
        };
        let torch = py.import_bound("torch")?;
        let kwargs = PyDict::new_bound(py);
        kwargs.set_item("dtype", torch_dtype(&torch, &spec.dtype)?)?;
        let tensor = torch.call_method("frombuffer", (memory,), Some(&kwargs))?;
        let shape = PyList::new_bound(py, &spec.shape);
        Ok(tensor.call_method1("reshape", (shape,))?.into_py(py))
    }
}

#[pyfunction]
fn open_tensor_file(path: &str) -> PyResult<TensorFile> {
    let file = File::open(path).map_err(|err| PyValueError::new_err(err.to_string()))?;
    let mmap = unsafe { MmapOptions::new().map(&file) }
        .map_err(|err| PyValueError::new_err(err.to_string()))?;
    let (data_start, tensors) = parse_safetensors_header(&mmap)?;
    Ok(TensorFile {
        mmap,
        data_start,
        tensors,
    })
}

#[pymodule]
fn _rust_pyo3(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<FixtureTokenizer>()?;
    m.add_class::<TensorFile>()?;
    m.add_function(wrap_pyfunction!(open_tensor_file, m)?)?;
    Ok(())
}
