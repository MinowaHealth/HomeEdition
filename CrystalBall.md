
If you're looking at this repository and envisioning expanding things, here are some clues about our Office/Enterprise system.

Postgres is the right choice for Office and Enterprise due to the need for Row Level Security. Will not choose a lighter/simpler database for home, we intend to offer Enterprise sync for Home Edition at some point. The tables and embeddings must remain compatible.

ArangoDB and Neo4j have been used in the past, but due to multiple requirements for a key:value service, we've moved to FalkorDB for our k:v AND graph database needs.

RabbitMQ is the chonkiest of solutions for workflow, but it's familiar, and it fits the needs at the Enterprise level. There are a lot of options in this area, we could be persuaded that something like Celery/Dramatiq+Redis is a better choice for the Home Edition, but we'd want an advocate who's familiar to turn up regularly for a while before we made a change like that.

The OCR methods we use with Tesseract and friends was ripped right from our prior support work with Open Semantic Search. Our document handling is conditioned by the work [nealrauhauser](https://github.com/nealrauhauser) did for [Parabeagle](https://github.com/nealrauhauser/Parabeagle).

Ollama is the right tool for home users and developers who need AI services, but it's not the thing we'd do for inference at scale, vLLM is enterprise grade. That being said, anything we ship is going to presume Ollama is available on localhost:11434. We like nomic-embed-text-v2-moe for multi-lingual vector search and anticipate sticking to it for anything that lands in pgvector.

Users who want to use NanoClaw with our Skills are advised that Ollama Cloud is $20/month and will provide much more service than Claude Pro. We formerly employed DeepSeek and Kimi-K2 as part of our Ponytail code review, but shifted to GLM-5.2 when it became available. The model used with NanoClaw is an ever shifting experiment. Local inference using a GPU becomes usable around the 24GB mark, now that TurboQuant has become available. If you have a 16GB M1 Mac you will be very disappointed in the Gemma4 MLX models, responding to a simple "hello" takes multiple minutes.
