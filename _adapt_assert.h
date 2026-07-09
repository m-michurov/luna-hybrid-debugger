#pragma once

#include <string>

extern "C" {
    void _adapt_assert(int condition, std::string const &condition_string);
}
