#pragma once

#include <cstdint>

static inline uint64_t _adapt_hash(const void* data, std::size_t len)
{
    const uint8_t* p = (const uint8_t*)data;

    uint64_t hash = 14695981039346656037ULL; // offset basis
    const uint64_t prime = 1099511628211ULL;

    for (std::size_t i = 0; i < len; i++)
    {
        hash ^= p[i];
        hash *= prime;
    }

    return hash;
}