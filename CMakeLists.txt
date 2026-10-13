cmake_minimum_required(VERSION 3.29)
project(EOSEmulator)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Keep absolute build paths out of the binaries.
#
# Several sources (libhv's logging macros among them) put __FILE__ into string
# literals, and fetched dependencies are compiled from an absolute path, so the
# raw build location ends up in the artefacts. Trim every root that can appear so
# the strings become relative.
function(eosemulator_trim_source_paths)
    set(prefixes "${CMAKE_SOURCE_DIR}" "${CMAKE_BINARY_DIR}")
    get_cmake_property(cache_vars CACHE_VARIABLES)
    foreach(var IN LISTS cache_vars)
        if(var MATCHES "^FETCHCONTENT_SOURCE_DIR_")
            list(APPEND prefixes "${${var}}")
        endif()
    endforeach()

    foreach(prefix IN LISTS prefixes)
        if(NOT prefix)
            continue()
        endif()
        string(REPLACE "\\" "/" unix "${prefix}")
        string(REPLACE "/" "\\" win "${prefix}")
        if(MSVC)
            # /d1trimfile matches against the path as the toolchain resolved it.
            # This MSVC-on-Linux wrapper runs under Wine, so __FILE__ carries a
            # drive prefix ("z:") and either separator style; a few call sites
            # also prepend a separator. Trim every shape.
            foreach(form
                    "${unix}/" "${unix}\\" "z:${unix}/" "z:${win}\\"
                    "\\${unix}/" "\\${win}\\")
                add_compile_options("/d1trimfile:${form}")
            endforeach()
        elseif(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
            add_compile_options("-fmacro-prefix-map=${unix}/=")
            add_compile_options("-ffile-prefix-map=${unix}/=")
        endif()
    endforeach()
endfunction()

eosemulator_trim_source_paths()

add_subdirectory(dependencies)
link_libraries(minhook)
include_directories(${plog_SOURCE_DIR}/include)

file(GLOB_RECURSE includes_deps
        "includes/*.hpp"
        "includes/*.h"

        "src/common/*.cpp"
        "src/common/*.hpp"
        "src/common/*.h"
)
include_directories(includes)
include_directories(src)

add_subdirectory(src/win)
add_subdirectory(src/linux)
