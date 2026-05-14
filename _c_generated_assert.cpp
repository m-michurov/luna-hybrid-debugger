#include <stdlib.h>
#include <stdio.h>
#include <ucenv.h>
#include <stdexcept>

using namespace luna::ucenv;

extern "C" {
    void _c_generated_assert(int condition, char const *condition_string) {
        if (condition) {
            return;
        }

        fprintf(stderr, "Assertion %s failed\n", condition_string);
        throw std::runtime_error("generated assertion failed");
        // exit(EXIT_FAILURE);
    }
}
