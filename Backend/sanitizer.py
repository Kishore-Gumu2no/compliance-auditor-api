from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
analyzer = AnalyzerEngine()
anonymizer = AnonymizerEngine()

def mask_pii(raw_text):
    results = analyzer.analyze(text = raw_text, entities = ["PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS"], language = "en")
    safe_text = anonymizer.anonymize(text = raw_text, analyzer_results= results)
    return safe_text.text

