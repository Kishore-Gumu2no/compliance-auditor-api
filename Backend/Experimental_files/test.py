from decimal import Decimal
from pydantic import BaseModel, field_validator
from datetime import date

def convert_time_to_decimal(hours: int, minutes:int):
    """The best part about Decimal(hours), Decimal(minutes) is that it does not care if you give it a string or integer, it will 
       convert both of them into a decimal number, it is designed to look at the value inside, if it a number hurray, converts to decimal
       if it something other than numbers like a, b, c, d, @, #, etc, it throws an error """
    
    return Decimal(hours) + (Decimal(minutes)/Decimal("60.00"))

"""The best part about Decimal(hours), Decimal(minutes) is that it does not care if you give it a string or integer, it will 
convert both of them into a decimal number, it is designed to look at the value inside, if it a number hurray, converts to decimal
if it something other than numbers like a, b, c, d, @, #, etc, it throws an error """

class WorkSession(BaseModel):
    """Our math engine needs absolute perfection, this would be the second schema that the data will have to pass through after the 
    LLM generates data, because the first schema(schema 1) will be built specifically to talk with AI, if the data doesnt follow schema 1, it 
    accepts the mess, clean and format it, BUT OUR BACKEND ENGINE CANNOT ACCEPT A MESS AND CLEAN IT, it needs a strict blueprint to 
    accept the input and calculate the real deal """

    hours: Decimal
    rate: Decimal
    actual_payout: Decimal
    shift_date: date

    @field_validator("hours") #This our bouncer to check the logic of value stored in hours which is not supposed to be > 24 hrs
    def validate_hours(cls, value):
        if value > Decimal("24.00"):
            raise ValueError("Hours cannot exceed 24")
        return value

def get_minimum_wage(shift_date):
    if (shift_date.year == 2023):
        return Decimal("15.00")
    elif (shift_date.year == 2024):
        return Decimal("16.50")
    else:
        return Decimal("0.00")

def calculate_difference(session):
    expected_payout = session.hours * session.rate
    difference = expected_payout - session.actual_payout
    if (difference > 0):
        status = "Underpaid"
    elif (difference == 0):
        status = "Paid correctly"
    elif (difference < 0):
        status = "Overpaid"
    return {"discrepancy": difference.quantize(Decimal('0.00')), "status": status}

llm_output = '{"shift_date": "2024-08-12", "hours": 8.5, "rate": 25.00, "actual_payout": 150.00}'
session = WorkSession.model_validate_json(llm_output)

""".model_validate_json is a method from inherited BaseModel class, this method takes the text from AI, reads the data in JSON format
line by line, then looks at the exact rules specified in WorkSession class and checks if the data(llm output) matches the rule book(WorkSession)
class perfectly. The things happening under the hood is:
    1. First it reads raw JSON text string and converts to basic dictionary.
    2. Takes the temporary dictionary and checks it against the class blueprint(WorkSession).
    3. If it checks, it converts the dictionary to a class object and returns it, if it doesnt check, it throws an error and stops the program"""

legal_wage = get_minimum_wage(session.shift_date)
result = calculate_difference(session)
print(result)
print(session.model_dump())
print(f"Worker paid: {session.rate}, Legal minimum: {legal_wage}")




file_bytes = await file.read()
with open("temp_worker_doc.pdf", "wb") as data_bytes:
       data_bytes.write(file_bytes)
       #region what's happening in the above line?
       """After opening the uploaded gig worker's pdf file we open a new blank file called data_bytes, this is like a variable(actually a file) for us
       to work inside the 'with open()' block, and we store the binary data read by 'await file.read()' in file_bytes, since we can't directly deal with 
       data_bytes inside the with block, we come out and call 'extract_pdf_text' from document_parser.py to extract information. """
       #endregion
try:
        extracted_text = extract_pdf_text("temp_worker_doc.pdf")
        #region File errors
        """ We are placing this line inside try block because if a .jpeg is renamed to .pdf and uploaded then inside try block 
        it shows us error, but outside try block this error will lead to system crash, which is not good."""
        #endregion
        initial_input = {'raw_text': extracted_text}
        #region What if there's no error in file but in AI
        """If the file is not mishandled or corrupt pdf is not uploaded but if the AI is offline, if the above line is outside the try block it would crash
        but here since it is inside the try block, in case it finds any error it jumps to except block."""
        #endregion
        final_state = await asyncio.to_thread(app.invoke, initial_input)
        return {
            "status": "Success",
            "final_result": final_state
        }
except Exception as e:
        return {
            "status": "Failed",
            "error_message": "Unfortunately the data you provided is not valid"
        }

 return { 
        "receipt_id" : math_result["receipt_id"],
        "shift_date": str(session.shift_date),
        "status": math_result["status"],
        "audit_details": {
            "hours_worked": float(session.hours),
            "rate": float(session.rate),
            "discrepancy": float(math_result["discrepancy"]),
            "Conclusion": math_result["status"]
        } }
