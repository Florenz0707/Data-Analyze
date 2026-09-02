from django.test import SimpleTestCase

from deepseek_api.streaming import encode_sse, iter_sse_lines


class StreamingProtocolTests(SimpleTestCase):
    def test_sse_encoding_and_chunked_parsing_preserve_unicode(self):
        frame = encode_sse("delta", {"type": "delta", "text": "部分回答"})

        events = list(iter_sse_lines(iter([frame[:8], frame[8:15], frame[15:]])))

        self.assertEqual(
            events,
            [{"event": "delta", "data": {"type": "delta", "text": "部分回答"}}],
        )
