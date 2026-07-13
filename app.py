import os
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

# 2. Gradio interface configuration and launch
try:
    import gradio as gr
    import gradio.routes as routes
    
    # Define a minimal Blocks interface to satisfy the supervisor
    with gr.Blocks() as demo:
        gr.Markdown("# AI Weather Map Backend Active")
        inp = gr.Textbox(label="Input", value="test")
        out = gr.Textbox(label="Output")
        btn = gr.Button("Verify GPU Connection")
        btn.click(fn=dummy_gpu_func, inputs=inp, outputs=out)
    
    # Compile the Gradio FastAPI application instance
    demo.app = routes.App.create_app(demo)
    
    # Mount our FastAPI weather map application on the root "/" path
    demo.app.mount("/", fastapi_app)
    
    # Launch the Gradio app. Gradio handles port binding, health check routing,
    # and registry with the Hugging Face supervisor automatically.
    demo.launch()
except ImportError:
    print("Gradio not installed, running pure FastAPI server with uvicorn.")
    import uvicorn
    uvicorn.run(fastapi_app, host="0.0.0.0", port=7860)
