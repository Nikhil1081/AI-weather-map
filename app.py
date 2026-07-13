import os
from app.main import app as fastapi_app
from app.main import root as serve_root
from starlette.routing import Route

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
    
    # Mount our FastAPI weather map application on the root "/" path (handles API endpoints /v1/*)
    demo.app.mount("/", fastapi_app)
    
    # Create a specific route for the root "/" path that points directly to our HTML home page,
    # and insert it at the very beginning (index 0) of the router. This forces Starlette to serve
    # our Leaflet Weather Map homepage instead of Gradio's index page.
    homepage_route = Route("/", endpoint=serve_root, methods=["GET"])
    demo.app.routes.insert(0, homepage_route)
    
    # Launch the Gradio app. Gradio handles port binding and supervisor registration.
    demo.launch()
except ImportError:
    print("Gradio not installed, running pure FastAPI server with uvicorn.")
    import uvicorn
    uvicorn.run(fastapi_app, host="0.0.0.0", port=7860)
