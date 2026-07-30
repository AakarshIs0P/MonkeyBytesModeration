import aiohttp
import json

from aiohttp.client_exceptions import ContentTypeError


class HTTPResponse:
    def __init__(self, status: int, response, res_method: str, headers):
        self.status = status
        self.response = response
        self.res_method = res_method
        self.headers = headers

    def __repr__(self) -> str:
        return f"<HTTPResponse status={self.status} res_method='{self.res_method}'>"


async def query(url, method="get", res_method="text", *args, **kwargs) -> HTTPResponse:
    async with aiohttp.ClientSession() as session:
        async with getattr(session, method.lower())(url, *args, **kwargs) as res:
            r = None
            try:
                r = await getattr(res, res_method)()
            except ContentTypeError:
                if res_method == "json":
                    try:
                        r = json.loads(await res.text())
                    except Exception:
                        r = None
            except Exception:
                r = None

            return HTTPResponse(
                status=res.status,
                response=r,
                res_method=res_method,
                headers=res.headers,
            )


async def get(url, *args, **kwargs) -> HTTPResponse:
    return await query(url, "get", *args, **kwargs)


async def post(url, *args, **kwargs) -> HTTPResponse:
    return await query(url, "post", *args, **kwargs)
