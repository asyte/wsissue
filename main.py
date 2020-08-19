from sanic import Sanic
from sanic import response
from sanic.log import logger
from aiofile import AIOFile

from db import db
import wslib
import secret

app = Sanic("Websocket Issue")
app.config.DB_USE_CONNECTION_FOR_REQUEST = True
app.config.DB_HOST = secret.DB_HOST
app.config.DB_PORT = secret.DB_PORT
app.config.DB_USER = secret.DB_USER
app.config.DB_PASSWORD = secret.DB_PASSWORD
app.config.DB_DATABASE = secret.DB_DATABASE
app.config.DB_POOL_MIN_SIZE = 1
app.config.DB_POOL_MAX_SIZE = 3

db.init_app(app)

@app.route("/")
async def index(request):
    index_html = ''
    async with AIOFile("./index.html", 'r') as afp:
        index_html = await afp.read()
    return response.HTTPResponse(index_html, content_type='text/html')

@app.middleware('response')
async def response_middleware(request, response):
    logger.debug(f'in response_middleware for {request.url}')

@app.websocket("/feed")
async def feed(request, ws):
    feed, is_existing = await wslib.Feed.get('feed')
    if not is_existing:
        feed.set_app(request.app)
    client = await feed.register(wslib.MyWSClient(ws, request))
    await client.receiver()
    return response.empty()
    

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8888)