# -*- coding: utf-8 -*-
# TencentBlueKing is pleased to support the open source community by making
# 蓝鲸智云 - PaaS 平台 (BlueKing - PaaS System) available.
# Copyright (C) 2017 THL A29 Limited, a Tencent company. All rights reserved.
# Licensed under the MIT License (the "License"); you may not use this file except
# in compliance with the License. You may obtain a copy of the License at
#
#     http://opensource.org/licenses/MIT
#
# Unless required by applicable law or agreed to in writing, software distributed under
# the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
# either express or implied. See the License for the specific language governing permissions and
# limitations under the License.
#
# We undertake not to change the open source license (MIT license) applicable
# to the current version of the project delivered to anyone in the future.

import logging
import socket
from typing import Sequence

from django.conf import settings
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import _KNOWN_SAMPLERS

from .instrumentor import BKAppInstrumentor

logger = logging.getLogger(__name__)


def _setup_otel_sdk_logging() -> None:
    """测试用：开启 OpenTelemetry SDK 内部日志（导出重试、队列满等）。"""
    logging.getLogger("opentelemetry").setLevel(logging.DEBUG)


class _DiagnosticSpanExporter(SpanExporter):
    """包装 OTLP exporter，记录每次 span 批量上报结果。"""

    def __init__(self, delegate: SpanExporter):
        self._delegate = delegate

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        result = self._delegate.export(spans)
        if result is SpanExportResult.SUCCESS:
            logger.info("OTLP exported %d span(s) successfully", len(spans))
        else:
            logger.warning("OTLP failed to export %d span(s)", len(spans))
        return result

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._delegate.force_flush(timeout_millis)


def setup_trace_config():
    _setup_otel_sdk_logging()
    # local environment use jaeger as trace service is easier
    # docker run -p 16686:16686 -p 6831:6831/udp jaegertracing/all-in-one

    # Note: Since v1.35, the Jaeger supports OTLP natively. Please use the OTLP exporter instead.
    # pypi ref: https://pypi.org/project/opentelemetry-exporter-jaeger/
    ip = socket.gethostbyname(socket.gethostname())
    trace.set_tracer_provider(
        tracer_provider=TracerProvider(
            resource=Resource.create(
                {
                    "service.name": settings.OTEL_SERVICE_NAME,
                    "bk.data.token": settings.OTEL_BK_DATA_TOKEN,
                    "net.host.ip": ip,
                },
            ),
            sampler=_KNOWN_SAMPLERS[settings.OTEL_SAMPLER],  # type: ignore
        )
    )
    otlp_exporter = _DiagnosticSpanExporter(OTLPSpanExporter(endpoint=settings.OTEL_GRPC_URL))
    span_processor = BatchSpanProcessor(otlp_exporter)
    trace.get_tracer_provider().add_span_processor(span_processor)  # type: ignore


def setup_by_settings():
    if settings.ENABLE_OTEL_TRACE:
        setup_trace_config()
        BKAppInstrumentor().instrument()
