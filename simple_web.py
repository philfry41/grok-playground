from flask import Flask, render_template_string

app = Flask(__name__)

@app.route('/')
def index():
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head><title>Test</title></head>
    <body>
        <h1>🎭 Grok Playground Web Interface</h1>
        <p>If you can see this, the web server is working!</p>
        <p>Status: ✅ Online</p>
    </body>
    </html>
    ''')

if __name__ == '__main__':
    print("Starting simple web server on port 8080...")
    app.run(host='0.0.0.0', port=8080, debug=False)
