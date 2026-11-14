# glass-memory

The words for a visual memory built from smart-glasses video: what is stored, how it is found
again, and when the glasses speak.

## Language

**Keyframe**:
One still saved from the glasses' video (one per second), kept next to its embedding.
_Avoid_: frame, snapshot

**Embedding**:
The EmbeddingGemma 2 vector of a keyframe, an object crop or a question. All three share one space, so they
can be compared.
_Avoid_: feature, latent

**Memory**:
One stored entry: an embedding, its keyframe and when it was seen.
_Avoid_: record, sample

**Retrieval**:
Picking the memories whose embeddings are closest to a question's embedding.

**Memory map**:
The 2D UMAP projection of all memories.

**Place**:
A cluster of scene embeddings the wearer keeps returning to.

**Transition**:
The moment the scene embedding moves from one place to another.

**Nudge**:
A sentence the glasses say without being asked.
