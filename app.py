import uvicorn
import os
import socket
from app.main import app as fastapi_app

# Find the first available port starting from the default to avoid bind conflicts
def find_available_port(start_port=7860):
    port = start_port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # If connect_ex returns non-zero, the port is not listening and is free
            if s.connect_ex(('127.0.0.1', port)) != 0:
                print(f"Found available port: {port}")
                return port
        print(f"Port {port} is currently in use, checking next port...")
        port += 1

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
    # Scan for a free port starting from the environment's target or 7860
    base_port = int(os.environ.get("PORT", os.environ.get("GRADIO_SERVER_PORT", 7860)))
    target_port = find_available_port(base_port)
    
    # Start the FastAPI application on the available port
    uvicorn.run(fastapi_app, host="0.0.0.0", port=target_port)
