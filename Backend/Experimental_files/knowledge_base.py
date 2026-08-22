from qdrant_client import QdrantClient
from fastembed import TextEmbedding
from qdrant_client.models import VectorParams, Distance, PointStruct

embedding_model = TextEmbedding("BAAI/bge-small-en-v1.5")

client = QdrantClient(":memory:") #This instruction creates a temporary database strictly inside my laptop's RAM
client.create_collection(
    collection_name = "labor_laws",
    vectors_config = VectorParams(size=384, distance = Distance.COSINE ) 
)
#region What is being done in the below line?
"""Here a collection is being created with the '.create_collection' method using the client instance and inside 
    this collection we are naming collection and defining the vector's configuration. Here, 'collection_name' and 
    'vectors_config' are parameters to collection's name and vector's configuration (dimensions and distance metric)."""
#endregion
laws = [
    'In 2023, the minimum wage is Rs. 15.00.', 
    'In 2024, the minimum wage is Rs. 16.50.'
]
vectors = list(embedding_model.embed(laws))
#region What's happening in the above line?
"""In the above line, we converting laws which is in English language to mathematical embedding by using TextEmbedding's instance
which is embedding_model and using the method '.embed', it is converted. We are storing the converted embedding in a list."""
#endregion
points = [
    PointStruct(id = 1, vector = vectors[0], payload= {"text": laws[0], "year": 2023}),
    PointStruct(id = 2, vector = vectors[1], payload= {"text": laws[1], "year":2024})
]
#region Points
"""We are constructing points here by assigning UUIDs, vectors and payload using 'PointStruct' in a list."""
    #endregion
client.upsert(collection_name="labor_laws", points = points)#This is the line which takes points and saves in Qdrant Database.
print("Labor laws suucessfully inserted into vector database")
print("Qdrant Vector Database initialized successfully")
print(client.collection_exists("labor_laws"))
query_text = "What is the legal baseline pay for the year 2024?" #search vector
query_text_vector = list(embedding_model.embed(query_text))[0]
#region Facts to know from above line
"""1. In the above line, 'embedding_model.embed(query_text)' generates a vector in list. Ex: [0.1, 0.2, -0.112......]
   2. We have used the method 'list' again so that we can store the generated vector inside another list, in order to easily
      access the element using their subscripts.
   3. query_text_vector = [[0.1, 0.2, -0.34, 0.11...]]
   4. THE BIGGEST MISCONCEPTION ABOUT EMBEDDING IS THAT 'one sentence = one embedding'. embed() FUNCTION DOESN'T CARE ABOUT 
      PUNCTUATION MARKS, EVEN IF WE GIVE AN ENTIRE PARAGRAPH IT WILL CONSIDER IT AS A SINGLE STRING SAVED IN THE LIST AND TAKES
      THE ENTIRE PARAGRAPH AND CONVERTS INTO A SINGLE EMBEDDING OF 386 DIMENSIONS."""
#endregion
search_results = client.query_points(
    collection_name = "labor_laws",
    query = query_text_vector,
    limit = 1
).points
#region What is this above block about
""" 1. In this block '.query_points' means nothing but 'go search for data' in plain English.
    2. When '.query_points' command finishes searching, it doesn't give simple python list containing ID, vector and payload. The 
       Qdrant database gives a large complex python object called 'QueryResponse', most of the contents inside that object is pretty 
       useless for our engine, such as time took to search, name of collection, etc. Therefore if we don't append the 'query_points()'
       with '.points' we will get this object. If we do append it with '.points' then python pulls the actual LIST of matched data.
    3. 'limit' value shows how many best matches we want from the database.  """
#endregion
print(search_results[0].payload)

