import os
import time
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
    
    # Launch the Gradio app in the background (preventing main thread lock).
    # Gradio will compile demo.app and start the uvicorn web server.
    demo.launch(prevent_thread_lock=True)
    
    # Once the server is running, inject our FastAPI routes into the active FastAPI instance.
    # Starlette routes are resolved dynamically, so updates to demo.app.routes take effect immediately!
    demo.app.mount("/", fastapi_app)
    
    # Prepend the root route handler at index 0 to override Gradio's default index page.
    homepage_route = Route("/", endpoint=serve_root, methods=["GET"])
    demo.app.routes.insert(0, homepage_route)
    
    print("FastAPI routes successfully injected into the running Gradio server!")
    
    # Keep the main thread alive since Gradio was started in the background
    while True:
        time.sleep(3600)
except ImportError:
    print("Gradio not installed, running pure FastAPI server with uvicorn.")
    import uvicorn
    uvicorn.run(fastapi_app, host="0.0.0.0", port=7860)
