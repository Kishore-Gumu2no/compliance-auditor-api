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
