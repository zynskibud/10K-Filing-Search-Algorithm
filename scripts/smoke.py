#!/usr/bin/env python
"""Smoke tests for Citation RAG environment setup."""

import os
import shutil
import subprocess
import sys

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Read .env first, so HF_HOME points inside the project before any HF import.
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import psycopg  # noqa: E402
import torch  # noqa: E402
from sentence_transformers import CrossEncoder, SentenceTransformer  # noqa: E402
from transformers import AutoTokenizer  # noqa: E402

# Test results
tests_passed = 0
tests_failed = 0


def test(name):
    """Decorator for tests."""
    def decorator(func):
        def wrapper():
            global tests_passed, tests_failed
            try:
                func()
                print(f"PASS: {name}")
                tests_passed += 1
            except Exception as e:
                print(f"FAIL: {name} - {e}")
                tests_failed += 1
        return wrapper
    return decorator


@test("Postgres reachable and vector extension works")
def test_postgres():
    """Test Postgres connection and vector extension."""
    conn = psycopg.connect(
        host="localhost",
        port=5433,
        user="rag",
        password="rag",
        dbname="rag",
    )
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("SELECT '[1,2,3]'::vector")
        result = cur.fetchone()
        assert result is not None
    conn.close()


@test("Docker container memory limit is 2g")
def test_docker_memory():
    """Test Docker container memory limit."""
    result = subprocess.run(
        ["docker", "inspect", "citation-rag-db", "--format", "{{.HostConfig.Memory}}"],
        capture_output=True,
        text=True,
    )
    memory_bytes = int(result.stdout.strip())
    # 2GB = 2147483648 bytes
    assert memory_bytes == 2147483648, f"Expected 2147483648, got {memory_bytes}"


@test("bge-small tokenizer loads from HF_HOME offline")
def test_tokenizer():
    """Test tokenizer loading from HF_HOME."""
    hf_home = os.environ.get("HF_HOME", "")
    assert hf_home, "HF_HOME not set"
    assert os.path.isabs(hf_home) and hf_home.startswith(PROJECT_ROOT), (
        f"HF_HOME must be inside the project: {hf_home}"
    )

    tokenizer = AutoTokenizer.from_pretrained(
        "BAAI/bge-small-en-v1.5",
        cache_dir=hf_home,
    )
    sentence = "This is a test sentence for tokenization."
    tokens = tokenizer.encode(sentence)
    assert len(tokens) > 0, "Failed to tokenize sentence"


@test("bge-small model loads on CPU")
def test_bge_small():
    """Test bge-small model loading and processing on CPU."""
    hf_home = os.environ.get("HF_HOME", "")
    model = SentenceTransformer(
        "BAAI/bge-small-en-v1.5",
        device="cpu",
        cache_folder=hf_home,
    )
    sentence = "This is a test sentence."
    embeddings = model.encode(sentence)
    assert embeddings is not None
    assert len(embeddings) > 0


@test("bge-m3 model loads on CPU")
def test_bge_m3():
    """Test bge-m3 model loading and processing on CPU."""
    hf_home = os.environ.get("HF_HOME", "")
    model = SentenceTransformer(
        "BAAI/bge-m3",
        device="cpu",
        cache_folder=hf_home,
    )
    sentence = "This is a test sentence."
    embeddings = model.encode(sentence)
    assert embeddings is not None
    assert len(embeddings) > 0


@test("bge-reranker-v2-m3 model loads on CPU")
def test_bge_reranker():
    """Test bge-reranker-v2-m3 model loading and processing on CPU."""
    hf_home = os.environ.get("HF_HOME", "")
    model = CrossEncoder(
        "BAAI/bge-reranker-v2-m3",
        device="cpu",
        cache_folder=hf_home,
    )
    sentence = "This is a test sentence."
    scores = model.predict([(sentence, sentence)])
    assert scores is not None
    assert len(scores) > 0


@test("ollama list shows qwen3:8b")
def test_ollama():
    """Test ollama list command."""
    result = subprocess.run(
        ["ollama", "list"],
        capture_output=True,
        text=True,
    )
    output = result.stdout
    assert "qwen3:8b" in output, "qwen3:8b not found in ollama list"


@test("Free disk >= 15 GB")
def test_free_disk():
    """Test free disk space."""
    result = subprocess.run(
        ["df", "-h", "/"],
        capture_output=True,
        text=True,
    )
    lines = result.stdout.strip().split("\n")
    # Second line has the actual mount
    parts = lines[1].split()
    available = parts[3]  # Available column

    # Parse the size (e.g., "43Gi" -> 43)
    if available.endswith("Gi"):
        size_gb = int(available[:-2])
    elif available.endswith("G"):
        size_gb = int(available[:-1])
    elif available.endswith("Ti"):
        size_gb = int(available[:-2]) * 1024
    elif available.endswith("T"):
        size_gb = int(available[:-1]) * 1024
    else:
        size_gb = 0

    assert size_gb >= 15, f"Free disk {size_gb} GB < 15 GB"


@test("torch MPS available")
def test_torch_mps():
    """Test torch MPS availability."""
    mps_available = torch.backends.mps.is_available()
    assert mps_available, "MPS not available"


if __name__ == "__main__":
    print("Running smoke tests...")
    print()

    test_postgres()
    test_docker_memory()
    test_tokenizer()
    test_bge_small()
    test_bge_m3()
    test_bge_reranker()
    test_ollama()
    test_free_disk()
    test_torch_mps()

    print()
    print(f"Tests passed: {tests_passed}")
    print(f"Tests failed: {tests_failed}")

    sys.exit(0 if tests_failed == 0 else 1)
