import json
import random
import string
import aioredis
import asyncio
import websockets

from functools import partial, wraps
from sanic import Sanic
from dataclasses import dataclass, field
from typing import Optional, Set

from model import InfoModel
import secret

def generate_code(length=12, include_punctuation=False):
    characters = string.ascii_letters + string.digits
    if include_punctuation:
        characters += string.punctuation
    return "".join(random.choice(characters) for x in range(length))

def build_message(action, payload, epicenter=None, **kwargs):
    msg = dict(action=action, payload=payload, **kwargs)
    return json.dumps(msg)

async def publish_message(channel, message):
    pub = await aioredis.create_redis(secret.REDIS_DRIVER, password=secret.REDIS_PASSWORD)
    await pub.publish(channel, message)
    pub.close()

def handler():
    def decorator(func):
        @wraps(func)
        async def handler_wrapper(self, payload):
            await func(self, payload)

        return handler_wrapper
    return decorator

@dataclass
class Client:
    interface: websockets.server.WebSocketServerProtocol = field(repr=False)
    sid: str = field(default_factory=partial(generate_code, 36))

    def __hash__(self):
        return hash(str(self))

    async def err(self, err_msg):
        message = build_message('err', {'msg': err_msg});
        await self.interface.send(message)

    async def transmit(self, message):
        await self.interface.send(message)
        
    async def ingest(self, message):
        message = json.loads(message)
        if 'action' in message and hasattr(self, message.get('action')):
            action = getattr(self, message.get('action'))
            await action(payload=message.get('payload', None))

    async def shutdown(self):
        await self.interface.close()

    async def receiver(self):
        while True:
            try:
                message = await self.interface.recv()
                if not message: break
                await self.ingest(message)

            except asyncio.CancelledError:
                await self.feed.unregister(self)
                break


class MyWSClient(Client):
    def __init__(self, websocket, request):
        self.request = request
        super().__init__(websocket)

    @handler()
    async def msg_from_user(self, payload):
        if 'msg' not in payload: return
        msg = payload['msg']

        info = await InfoModel.query.gino.first()

        message = build_message('msg_from_server', {'msg': info.some_value })
        await self.interface.send(message)


class FeedCache(dict):
    def __repr__(self):
        return str({k: len(v.clients) for k, v in self.items()})


class Feed:
    name: str
    app: Sanic
    clients: Set[Client]
    cache = FeedCache()
    lock: asyncio.Lock

    def __init__(self, name):
        self.name = name
        self.clients = set()
        self.lock = asyncio.Lock()

    def set_app(self, app):
        self.app = app


    @classmethod
    async def get(cls, name: str):
        is_existing = False

        if name in cls.cache:
            feed = cls.cache[name]
            await feed.acquire_lock()
            is_existing = True
        else:
            feed = cls(name=name)
            await feed.acquire_lock()

            feed.pool = await aioredis.create_redis_pool(
                address=REDIS_DRIVER,
                password=REDIS_PASSWORD,
                minsize=2,
                maxsize=500,
                encoding="utf-8",
            )

            cls.cache[name] = feed

            channels = await feed.pool.subscribe(name)
            if channels:
                feed.pubsub = channels[0]

        if not is_existing:
            loop = asyncio.get_event_loop()
            loop.create_task(feed.receiver())

        return feed, is_existing

    async def acquire_lock(self) -> None:
        if not self.lock.locked():
            await self.lock.acquire()

    async def receiver(self) -> None:

        try:
            while await self.pool.wait_closed():
                await self.pubsub.wait_message()
                outbound = await self.pubsub.get(encoding='utf-8')
                if outbound:
                    for client in self.clients:
                        try:
                            await client.transmit(outbound)
                        except websockets.exceptions.ConnectionClosed:
                            await self.unregister(client)
                            pass

        except aioredis.ChannelClosedError:
            pass

    async def register(self, client) -> Optional[Client]:
        client.feed = self
        self.clients.add(client)
        return client

    async def unregister(self, client: Client) -> None:
        if client in self.clients:
            await client.shutdown()
            self.clients.remove(client)

            if len(self.clients) == 0:
                self.lock.release()
                await self.pool.unsubscribe(self.name)
                await self.destroy()

    async def destroy(self) -> None:
        if not self.lock.locked():
            del self.cache[self.name]
            self.pool.close()

    async def publish(self, message: str) -> None:
        await self.pool.execute("publish", self.name, message)