
from core.models import ClientDataSource, DataSource
from rest_framework.viewsets import ModelViewSet
from oauth2_provider.models import get_application_model
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import action

from core.serializers import ClientDataSourceSerializer, ClientSerializer, DataSourceSerializer

Application = get_application_model()


class ClientViewSet(ModelViewSet):
    serializer_class = ClientSerializer

    def get_queryset(self):
        return Application.objects.all().order_by("-created")

    def partial_update(self, request, *args, **kwargs):
        # print("keys in request.data:", list(request.data.keys()))
        # print("parsers:", [p.__class__.__name__ for p in self.get_parsers()])
        # print("renderers:", [r.__class__.__name__ for r in self.get_renderers()])
        # print("\n=== ClientViewSet.partial_update HIT ===")
        # print("path:", request.get_full_path())
        # print("kwargs:", kwargs)
        # print("content-type:", request.content_type)
        # print("raw request.data:", request.data)

        instance = self.get_object()
        # print("BEFORE instance:", {"id": instance.id, "name": instance.name, "client_id": instance.client_id})

        serializer = self.get_serializer(instance, data=request.data, partial=True)
        # print("serializer class:", serializer.__class__.__name__)

        serializer.is_valid(raise_exception=True)
        # print("validated_data:", serializer.validated_data)

        self.perform_update(serializer)  # calls serializer.save()

        # re-fetch + re-serialize so response includes computed fields like codeVerifier
        instance.refresh_from_db()
        out = self.get_serializer(instance).data

        # print("AFTER instance:", {"id": instance.id, "name": instance.name, "client_id": instance.client_id})
        # print("response data:", out)
        # print("=== END partial_update ===\n")

        return Response(out, status=status.HTTP_200_OK)

    @action(detail=True, methods=["GET", "POST", "DELETE"])
    def data_sources(self, request, pk):
        if request.method == "GET":
            data_sources = DataSource.objects.filter(client_applications__client_id=pk).distinct()
            serializer = DataSourceSerializer(data_sources, many=True)
            return Response(serializer.data)
        else:
            response = None
            if request.method == "POST":
                response = ClientDataSource.objects.create(
                    client_id=pk, data_source_id=request.data["data_source_id"]
                )
            else:
                response = ClientDataSource.objects.filter(
                    client_id=pk, data_source_id=request.data["data_source_id"]
                ).delete()

            return Response(ClientDataSourceSerializer(response, many=False).data)
