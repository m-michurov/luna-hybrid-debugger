#include <stdlib.h>
#include <stdio.h>
#include <ucenv.h>
#include <stdexcept>

using namespace luna::ucenv;

extern "C" {
    void _adapt_assert(int condition, std::string const &condition_string) {
        if (condition) {
            return;
        }

        fprintf(stderr, "Assertion failed: %s\n", condition_string.c_str());
        throw std::runtime_error("generated assertion failed");
        // exit(EXIT_FAILURE);
    }
}
