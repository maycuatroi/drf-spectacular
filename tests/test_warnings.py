from typing import Union
from unittest import mock

import pytest
from django.db import models
from django.urls import path
from rest_framework import mixins, serializers, views, viewsets
from rest_framework.authentication import BaseAuthentication
from rest_framework.decorators import action, api_view
from rest_framework.schemas import AutoSchema as DRFAutoSchema
from rest_framework.views import APIView

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter, OpenApiRequest, PolymorphicProxySerializer, extend_schema, extend_schema_view,
    inline_serializer,
)
from tests import generate_schema
from tests.models import SimpleModel, SimpleSerializer


def test_serializer_name_reuse(capsys):
    from rest_framework import routers

    router = routers.SimpleRouter()

    def x1():
        class XSerializer(serializers.Serializer):
            uuid = serializers.UUIDField()

        return XSerializer

    def x2():
        class XSerializer(serializers.Serializer):
            integer = serializers.IntegerField()

        return XSerializer

    class X1Viewset(mixins.ListModelMixin, viewsets.GenericViewSet):
        serializer_class = x1()

    router.register('x1', X1Viewset, basename='x1')

    class X2Viewset(mixins.ListModelMixin, viewsets.GenericViewSet):
        serializer_class = x2()

    router.register('x2', X2Viewset, basename='x2')

    generate_schema(None, patterns=router.urls)

    stderr = capsys.readouterr().err
    assert 'Encountered 2 components with identical names "X" and different identities' in stderr


def test_owned_serializer_naming_override_with_ref_name_collision(warnings):
    class XSerializer(serializers.Serializer):
        x = serializers.UUIDField()

    class YSerializer(serializers.Serializer):
        x = serializers.IntegerField()

        class Meta:
            ref_name = 'X'  # already used above

    class XAPIView(APIView):
        @extend_schema(request=XSerializer, responses=YSerializer)
        def post(self, request):
            pass  # pragma: no cover

    generate_schema('x', view=XAPIView)


def test_no_queryset_warn(capsys):
    class X1Serializer(serializers.Serializer):
        uuid = serializers.UUIDField()

    class X1Viewset(viewsets.ReadOnlyModelViewSet):
        serializer_class = X1Serializer

    generate_schema('x1', X1Viewset)
    stderr = capsys.readouterr().err
    assert (
        'could not derive type of path parameter "id" because it '
        'is untyped and obtaining queryset from the viewset failed.'
    ) in stderr


def test_path_param_not_in_model(capsys):
    class XViewset(viewsets.ReadOnlyModelViewSet):
        serializer_class = SimpleSerializer
        queryset = SimpleModel.objects.none()

        @action(detail=True, url_path='meta/(?P<ephemeral>[^/.]+)', methods=['POST'])
        def meta_param(self, request, ephemeral, pk):
            pass  # pragma: no cover

    generate_schema('x1', XViewset)
    stderr = capsys.readouterr().err
    assert 'no such field' in stderr
    assert 'XViewset' in stderr


def test_no_authentication_scheme_registered(capsys):
    class XAuth(BaseAuthentication):
        pass  # pragma: no cover

    class XSerializer(serializers.Serializer):
        uuid = serializers.UUIDField()

    class XViewset(mixins.ListModelMixin, viewsets.GenericViewSet):
        serializer_class = XSerializer
        authentication_classes = [XAuth]

    generate_schema('x', XViewset)
    stderr = capsys.readouterr().err
    assert 'no OpenApiAuthenticationExtension registered' in stderr
    assert 'XViewset' in stderr


def test_serializer_not_found(capsys):
    class XViewset(mixins.ListModelMixin, viewsets.GenericViewSet):
        pass  # pragma: no cover

    generate_schema('x', XViewset)
    assert (
        'Error [XViewset]: exception raised while getting serializer.'
    ) in capsys.readouterr().err


def test_extend_schema_unknown_class(capsys):
    class DoesNotCompute:
        pass  # pragma: no cover

    class X1Viewset(viewsets.GenericViewSet):
        @extend_schema(responses={200: DoesNotCompute})
        def list(self, request):
            pass  # pragma: no cover

    generate_schema('x1', X1Viewset)
    assert 'Expected either a serializer' in capsys.readouterr().err


def test_extend_schema_unknown_class2(capsys):
    class DoesNotCompute:
        pass  # pragma: no cover

    class X1Viewset(viewsets.GenericViewSet):
        @extend_schema(responses=DoesNotCompute)
        def list(self, request):
            pass  # pragma: no cover

    generate_schema('x1', X1Viewset)
    assert 'Expected either a serializer' in capsys.readouterr().err


def test_no_serializer_class_on_apiview(capsys):
    class XView(views.APIView):
        def get(self, request):
            pass  # pragma: no cover

    generate_schema('x', view=XView)
    assert 'Error [XView]: unable to guess serializer.' in capsys.readouterr().err


def test_unable_to_follow_field_source_through_intermediate_property_warning(capsys):
    class FailingFieldSourceTraversalModel1(models.Model):
        @property
        def x(self):  # missing type hint emits warning
            return  # pragma: no cover

    class XSerializer(serializers.ModelSerializer):
        x = serializers.ReadOnlyField(source='x.y')

        class Meta:
            model = FailingFieldSourceTraversalModel1
            fields = '__all__'

    class XAPIView(APIView):
        @extend_schema(responses=XSerializer)
        def get(self, request):
            pass  # pragma: no cover

    generate_schema('x', view=XAPIView)
    assert (
        '[XAPIView > XSerializer]: could not follow field source through intermediate property'
    ) in capsys.readouterr().err


def test_unable_to_derive_function_type_warning(capsys):
    class FailingFieldSourceTraversalModel2(models.Model):
        @property
        def x(self):  # missing type hint emits warning
            return  # pragma: no cover

    class XSerializer(serializers.ModelSerializer):
        x = serializers.ReadOnlyField()
        y = serializers.SerializerMethodField()

        def get_y(self):
            return  # pragma: no cover

        class Meta:
            model = FailingFieldSourceTraversalModel2
            fields = '__all__'

    class XAPIView(APIView):
        @extend_schema(responses=XSerializer)
        def get(self, request):
            pass  # pragma: no cover

    generate_schema('x', view=XAPIView)
    stderr = capsys.readouterr().err
    assert '[XAPIView > XSerializer]: unable to resolve type hint for function "x"' in stderr
    assert '[XAPIView > XSerializer]: unable to resolve type hint for function "get_y"' in stderr


def test_unable_to_traverse_union_type_hint(capsys):
    class Foo:
        foo_value: int = 1

    class Bar:
        pass

    class FailingFieldSourceTraversalModel3(models.Model):
        @property
        def foo_or_bar(self) -> Union[Foo, Bar]:  # type: ignore
            pass  # pragma: no cover

    class XSerializer(serializers.ModelSerializer):
        foo_value = serializers.ReadOnlyField(source='foo_or_bar.foo_value')

        class Meta:
            model = FailingFieldSourceTraversalModel3
            fields = '__all__'

    class XAPIView(APIView):
        @extend_schema(responses=XSerializer)
        def get(self, request):
            pass  # pragma: no cover

    generate_schema('foo_value', view=XAPIView)
    stderr = capsys.readouterr().err
    assert 'could not traverse Union type' in stderr
    assert 'Foo' in stderr
    assert 'Bar' in stderr


def test_operation_id_collision_resolution(capsys):
    @extend_schema(responses=OpenApiTypes.FLOAT)
    @api_view(['GET'])
    def view_func(request, format=None):
        pass  # pragma: no cover

    urlpatterns = [
        path('pi/<int:foo>', view_func),
        path('pi/', view_func),
    ]
    schema = generate_schema(None, patterns=urlpatterns)

    assert schema['paths']['/pi/']['get']['operationId'] == 'pi_retrieve'
    assert schema['paths']['/pi/{foo}']['get']['operationId'] == 'pi_retrieve_2'
    assert 'operationId "pi_retrieve" has collisions' in capsys.readouterr().err


@mock.patch('rest_framework.settings.api_settings.DEFAULT_SCHEMA_CLASS', DRFAutoSchema)
def test_compatible_auto_schema_class_on_view(no_warnings):
    @extend_schema(responses=OpenApiTypes.FLOAT)
    @api_view(['GET'])
    def view_func(request):
        pass  # pragma: no cover

    with pytest.raises(AssertionError) as excinfo:
        generate_schema('/x/', view_function=view_func)
    assert "Incompatible AutoSchema" in str(excinfo.value)


def test_extend_schema_view_on_missing_view_method(capsys):
    @extend_schema_view(
        post=extend_schema(tags=['tag'])
    )
    class XAPIView(APIView):
        def get(self, request):
            pass  # pragma: no cover

    generate_schema('x', view=XAPIView)
    stderr = capsys.readouterr().err
    assert '@extend_schema_view argument "post" was not found on view' in stderr


def test_polymorphic_proxy_subserializer_missing_type_field(capsys):
    class IncompleteSerializer(serializers.Serializer):
        field = serializers.IntegerField()  # missing field named type

    class XAPIView(APIView):
        @extend_schema(
            responses=PolymorphicProxySerializer(
                component_name='Broken',
                serializers=[IncompleteSerializer],
                resource_type_field_name='type',
            )
        )
        def get(self, request):
            pass  # pragma: no cover

    generate_schema('x', view=XAPIView)
    stderr = capsys.readouterr().err
    assert 'sub-serializer Incomplete of Broken must contain' in stderr


@pytest.mark.parametrize('resource_type_field_name', ['field', None])
def test_polymorphic_proxy_serializer_misconfig(capsys, resource_type_field_name):
    class XSerializer(serializers.Serializer):
        field = serializers.IntegerField()  # missing field named type

    class XAPIView(APIView):
        @extend_schema(
            responses=PolymorphicProxySerializer(
                component_name='Broken',
                serializers=[XSerializer(many=True)],
                resource_type_field_name=resource_type_field_name,
            )
        )
        def get(self, request):
            pass  # pragma: no cover

    generate_schema('x', view=XAPIView)
    stderr = capsys.readouterr().err
    assert 'following usage pattern is necessary: PolymorphicProxySerializer' in stderr


def test_warning_operation_id_on_extend_schema_view(capsys):
    from drf_spectacular.drainage import GENERATOR_STATS

    @extend_schema(operation_id='Invalid', responses=int)
    class XAPIView(APIView):
        def get(self, request):
            pass  # pragma: no cover

    generate_schema('x', view=XAPIView)
    stderr = capsys.readouterr().err
    # check basic emittance of error message to stdout
    assert 'using @extend_schema on viewset class XAPIView with parameters' in stderr
    # check that delayed error message was persisted on view class
    assert getattr(XAPIView, '_spectacular_annotation', {}).get('errors')
    # check that msg survived pre-generation warning/error cache reset
    assert 'using @extend_schema on viewset' in list(GENERATOR_STATS._error_cache.keys())[0]


def test_warning_request_body_not_resolvable(capsys):
    class Dummy:
        pass

    @extend_schema(request=Dummy)
    @api_view(['POST'])
    def view_func(request, format=None):
        pass  # pragma: no cover

    generate_schema('x', view_function=view_func)
    stderr = capsys.readouterr().err
    assert 'could not resolve request body for POST /x' in stderr


def test_response_header_warnings(capsys):
    @extend_schema(
        request=OpenApiTypes.ANY,
        responses=OpenApiTypes.ANY,
        parameters=[OpenApiParameter(name='test', response=True)]
    )
    @api_view(['POST'])
    def view_func(request, format=None):
        pass  # pragma: no cover

    generate_schema('x', view_function=view_func)
    stderr = capsys.readouterr().err
    assert 'incompatible location type ignored' in stderr


def test_unknown_base_field_warning(capsys):
    class UnknownField(serializers.Field):
        pass

    class XSerializer(serializers.Serializer):
        field = UnknownField()

    @extend_schema(request=XSerializer, responses=XSerializer)
    @api_view(['POST'])
    def view_func(request, format=None):
        pass  # pragma: no cover

    generate_schema('x', view_function=view_func)
    stderr = capsys.readouterr().err
    assert 'could not resolve serializer field' in stderr


def test_warning_read_only_field_on_non_model_serializer(capsys):
    class XSerializer(serializers.Serializer):
        field = serializers.ReadOnlyField()

    class XViewSet(viewsets.ModelViewSet):
        serializer_class = XSerializer
        queryset = SimpleModel.objects.all()

    # test validity of serializer construction
    serializer = XSerializer(instance={'field': 1})
    serializer.data

    generate_schema('x', XViewSet)
    stderr = capsys.readouterr().err
    assert 'Could not derive type for ReadOnlyField "field"' in stderr


def test_warning_missing_lookup_field_on_model_serializer(capsys):
    class XViewSet(viewsets.ModelViewSet):
        serializer_class = SimpleSerializer
        queryset = SimpleModel.objects.all()
        lookup_field = 'non_existent_field'

    generate_schema('x', XViewSet)
    stderr = capsys.readouterr().err
    assert (
        'could not derive type of path parameter "non_existent_field" because model '
        '"tests.models.SimpleModel" contained no such field.'
    ) in stderr


@mock.patch(
    'drf_spectacular.settings.spectacular_settings.PATH_CONVERTER_OVERRIDES', {'int': object}
)
def test_invalid_path_converter_override(capsys):
    @extend_schema(responses=OpenApiTypes.FLOAT)
    @api_view(['GET'])
    def pi(request, foo):
        pass  # pragma: no cover

    urlpatterns = [path('/a/<int:var>/', pi)]
    generate_schema(None, patterns=urlpatterns)
    stderr = capsys.readouterr().err
    assert 'Unable to use path converter override for "int".' in stderr


def test_malformed_vendor_extensions(capsys):
    @extend_schema(
        responses=None,
        extensions={'foo': 'not-prefixed'}
    )
    @api_view(['GET'])
    def view_func(request, format=None):
        pass  # pragma: no cover

    generate_schema('x', view_function=view_func)
    stderr = capsys.readouterr().err
    assert 'invalid extension \'foo\'. vendor extensions must start with' in stderr


def test_serializer_method_missing(capsys):
    class XSerializer(serializers.Serializer):
        some_field = serializers.SerializerMethodField()

    @extend_schema(responses=XSerializer)
    @api_view(['GET'])
    def view_func(request):
        pass  # pragma: no cover

    generate_schema('x', view_function=view_func)
    stderr = capsys.readouterr().err
    assert 'SerializerMethodField "some_field" is missing required method "get_some_field"' in stderr


def test_invalid_field_names(capsys):
    @extend_schema(
        responses=inline_serializer(
            'Name with spaces',
            fields={'test': serializers.CharField()}
        ),
    )
    @api_view(['GET'])
    def view_func(request, format=None):
        pass  # pragma: no cover

    generate_schema('/x/', view_function=view_func)

    stderr = capsys.readouterr().err
    assert 'illegal characters' in stderr


@pytest.mark.parametrize('type_arg,many', [
    (SimpleSerializer, True),
    (SimpleSerializer(many=True), None),
    (serializers.ListSerializer(child=serializers.CharField()), None),
    (serializers.ListField(child=serializers.CharField()), None),
])
def test_invalid_parameter_types(capsys, type_arg, many):
    @extend_schema(
        request=OpenApiTypes.ANY,
        responses=OpenApiTypes.ANY,
        parameters=[OpenApiParameter(name='test', type=type_arg, many=many)]
    )
    @api_view(['POST'])
    def view_func(request, format=None):
        pass  # pragma: no cover

    generate_schema('/x/', view_function=view_func)
    stderr = capsys.readouterr().err
    assert 'parameter "test"' in stderr


def test_primary_key_related_field_without_serializer_meta(capsys):
    class XSerializer(serializers.Serializer):
        field = serializers.PrimaryKeyRelatedField(read_only=True)

    @extend_schema(responses=XSerializer, request=XSerializer)
    @api_view(['GET'])
    def view_func(request, format=None):
        pass  # pragma: no cover

    generate_schema('/x/', view_function=view_func)
    stderr = capsys.readouterr().err
    assert 'Could not derive type for under-specified PrimaryKeyRelatedField "field"' in stderr


def test_request_encoding_on_invalid_content_type(capsys):
    class XSerializer(serializers.Serializer):
        field = serializers.MultipleChoiceField(choices=[1, 2, 3, 4])

    @extend_schema(
        request={
            'application/msgpack': OpenApiRequest(
                request=XSerializer,
                encoding={"field": {"style": "form", "explode": True}},
            )
        },
        responses=XSerializer
    )
    @api_view(['POST'])
    def view_func(request, format=None):
        pass  # pragma: no cover

    generate_schema('/x/', view_function=view_func)
    stderr = capsys.readouterr().err
    assert 'Encodings object on media types other than' in stderr


def test_abstract_model_serializer_warning(capsys):
    class AbstractModel(models.Model):
        field = models.CharField(max_length=10)

        class Meta:
            abstract = True

    class XSerializer(serializers.ModelSerializer):
        class Meta:
            model = AbstractModel
            fields = '__all__'

    class XAPIView(APIView):
        serializer_class = XSerializer

        def get(self, request):
            pass  # pragma: no cover

    generate_schema('x', view=XAPIView)
    stderr = capsys.readouterr().err
    assert 'abstract model' in stderr
