"""Allow browser embeds on any website to call /api/widget/."""


class WidgetCorsMiddleware:
    WIDGET_PREFIX = '/api/widget/'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(self.WIDGET_PREFIX) and request.method == 'OPTIONS':
            from django.http import HttpResponse

            response = HttpResponse(status=204)
            self._apply(response, request)
            return response

        response = self.get_response(request)
        if request.path.startswith(self.WIDGET_PREFIX):
            self._apply(response, request)
        return response

    @staticmethod
    def _apply(response, request):
        origin = request.headers.get('Origin', '*') or '*'
        response['Access-Control-Allow-Origin'] = origin
        response['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response['Access-Control-Max-Age'] = '86400'
        response['Vary'] = 'Origin'
