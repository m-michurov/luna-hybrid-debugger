static inline uint64_t _adapt_fnv1a64_hash(const void* data, size_t len)
{
    const uint8_t* p = (const uint8_t*)data;

    uint64_t hash = 14695981039346656037ULL; // offset basis
    const uint64_t prime = 1099511628211ULL;

    for (size_t i = 0; i < len; i++)
    {
        hash ^= p[i];
        hash *= prime;
    }

    return hash;
}