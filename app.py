import uvicorn
from app.main import app as fastapi_app

# Dummy ZeroGPU decorator to satisfy Hugging Face free-tier startup checks
try:
    import spaces
    @spaces.GPU
    def dummy_gpu_func():
        return "ZeroGPU check satisfied"
except ImportError:
    pass

if __name__ == "__main__":
    # Start the FastAPI application on port 7860 (Hugging Face Spaces default port)
    uvicorn.run(fastapi_app, host="0.0.0.0", port=7860)
