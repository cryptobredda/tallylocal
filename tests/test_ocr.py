"""OCR ships in the image: PP-OCRv6 through RapidOCR, CPU only."""
from app.ocr_service import ocr_service


def test_ocr_engine_is_available_and_identified():
    status = ocr_service.status()
    assert status['available'] is True
    assert status['engine'] == 'rapidocr'
    assert 'RapidOCR' in status['name']
    assert 'PP-OCR' in status['name']


def test_status_is_json_safe():
    import json
    json.dumps(ocr_service.status())


def test_models_are_bundled_with_the_package():
    """No runtime download: the image must work offline."""
    import pathlib
    import rapidocr
    models = pathlib.Path(rapidocr.__file__).parent / 'models'
    onnx = list(models.glob('*.onnx'))
    assert onnx, 'no ONNX models bundled; the container would try to download them'
    assert any('det' in p.name for p in onnx)
    assert any('rec' in p.name for p in onnx)
