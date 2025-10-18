#!/bin/bash
# Quick preview script for JupyterHealth Exchange documentation

echo "Building JupyterHealth Exchange documentation..."
make clean > /dev/null 2>&1
make html

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Documentation built successfully!"
    echo ""
    echo "Opening documentation in browser..."
    open build/html/index.html

    echo ""
    echo "You can also run a live server with auto-reload:"
    echo "  make livehtml"
    echo ""
    echo "This will serve at: http://localhost:8000"
else
    echo "❌ Build failed. Check the errors above."
fi