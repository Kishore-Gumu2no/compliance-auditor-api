from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
analyzer = AnalyzerEngine()
anonymizer = AnonymizerEngine()

# Shared by text masking and image redaction so both protect the same data.
PII_ENTITIES = ["PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS"]

def mask_pii(raw_text):
    results = analyzer.analyze(text = raw_text, entities = PII_ENTITIES, language = "en")
    safe_text = anonymizer.anonymize(text = raw_text, analyzer_results= results)
    return safe_text.text
