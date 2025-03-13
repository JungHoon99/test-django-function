from rest_framework.viewsets import ModelViewSet

import json

from .models import Post
from .serializers import PostSerializer


class PostViewSet(ModelViewSet):
    queryset = Post.objects.all()
    serializer_class = PostSerializer
    permission_classes = []
    authentication_classes = []

    def perform_create(self, serializer):
        content = self.request.data.get('content', '')
        serializer.save(content=json.dumps(content))
