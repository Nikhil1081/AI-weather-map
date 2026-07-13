import uvicorn
import socket
import time
import subprocess
from app.main import app as fastapi_app

# Port-freeing helper to resolve address-in-use conflicts during quick restarts
def ensure_port_is_free(port=7860):
    print(f"Verifying port {port} availability...")
    for i in range(10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # If connect_ex returns non-zero, the port is free and not listening
            if s.connect_ex(('127.0.0.1', port)) != 0:
                print(f"Port {port} is free and ready for binding.")
                return True
        print(f"[Attempt {i+1}/10] Port {port} is in use. Waiting 2s for socket cleanup...")
        time.sleep(2)
    
    # If still blocked, attempt to terminate the zombie process holding the port
    try:
        print(f"Port {port} is still blocked. Attempting to kill process using fuser...")
        subprocess.run(f"fuser -k {port}/tcp", shell=True, capture_output=True)
        time.sleep(1)
    except Exception as e:
        print(f"Could not kill process holding port: {e}")
    return False

# 1. ZeroGPU function definition linked to Gradio event listener
try:
    import spaces
    @spaces.GPU
    def dummy_gpu_func(x):
        return f"ZeroGPU check satisfied: {x}"
except ImportError:
    def dummy_gpu_func(x):
        return f"Standard CPU: {x}"

# 2. Gradio interface configuration
try:
    import gradio as gr
    with gr.Blocks() as demo:
        gr.Markdown("# AI Weather Map Backend Active")
        inp = gr.Textbox(label="Input", value="test")
        out = gr.Textbox(label="Output")
        btn = gr.Button("Verify GPU Connection")
        btn.click(fn=dummy_gpu_func, inputs=inp, outputs=out)
    
    # Mount Gradio onto the existing FastAPI app
    fastapi_app = gr.mount_gradio_app(fastapi_app, demo, path="/gradio")
except ImportError:
    print("Gradio not installed, running pure FastAPI server.")

if __name__ == "__main__":
    # Ensure port 7860 is free before launching uvicorn
    ensure_port_is_free(7860)
    
    # Start the FastAPI application on port 7860
    uvicorn.run(fastapi_app, host="0.0.0.0", port=7860)
