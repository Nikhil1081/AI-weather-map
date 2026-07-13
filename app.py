import uvicorn
import os
from app.main import app as fastapi_app

# Diagnostics: Print all environment variables to identify the correct port
print("--- STARTUP DIAGNOSTICS ---")
print("Environment variables:")
for k, v in sorted(os.environ.items()):
    if "KEY" not in k and "TOKEN" not in k and "PASS" not in k: # Hide secrets
        print(f"  {k}: {v}")
print("---------------------------")

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
    # Hugging Face sets specific environment variables for ports.
    # We fallback to 7860 if none are specified.
    port = int(os.environ.get("PORT", os.environ.get("GRADIO_SERVER_PORT", 7860)))
    print(f"Resolved target binding port: {port}")
    
    # Start the FastAPI application on the resolved port
    uvicorn.run(fastapi_app, host="0.0.0.0", port=port)
