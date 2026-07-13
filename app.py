import uvicorn
from app.main import app as fastapi_app

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
    # Start the FastAPI application on port 7860 instantly (no sleeps)
    uvicorn.run(fastapi_app, host="0.0.0.0", port=7860)
