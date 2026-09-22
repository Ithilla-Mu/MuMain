# Windows - WSL + MinGW (cross-compile)

Cross-compile the Windows client from WSL/Linux with the MinGW-w64 toolchain.
This is a fast development loop (native ext4 I/O) and what the CI uses.

See [the build guide](../README.md) for shared concepts. For a native *Linux*
client instead, see [../linux/console.md](../linux/console.md).

> **Keep the repo on the WSL filesystem** (`/home/<user>/MuMain`), not under
> `/mnt/c/...`. The Windows-filesystem path goes through a slow translation
> layer.

## Prerequisites (one-time)

```bash
sudo apt-get update
# 64-bit target:
sudo apt-get install -y mingw-w64 g++-mingw-w64-x86-64 cmake ninja-build
# 32-bit target instead:
sudo apt-get install -y mingw-w64 g++-mingw-w64-i686 cmake ninja-build
```

You also need a **static** MinGW `libturbojpeg.a` (so the client doesn't depend
on `libturbojpeg.dll`). Build it once per arch, e.g. for x86_64:

```bash
git clone --depth 1 --branch 3.1.3 https://github.com/libjpeg-turbo/libjpeg-turbo.git _deps/libjpeg-turbo
cmake -S _deps/libjpeg-turbo -B _deps/build-turbo \
  -DCMAKE_SYSTEM_NAME=Windows -DCMAKE_SYSTEM_PROCESSOR=x86_64 \
  -DCMAKE_C_COMPILER=x86_64-w64-mingw32-gcc \
  -DCMAKE_BUILD_TYPE=Release -DENABLE_SHARED=OFF -DENABLE_STATIC=ON -DWITH_SIMD=OFF \
  -DCMAKE_INSTALL_PREFIX="$PWD/_deps/mingw-x86_64"
cmake --build _deps/build-turbo -j"$(nproc)" && cmake --install _deps/build-turbo
```

(Replace `x86_64` with `i686` / `i686-w64-mingw32-gcc` for the 32-bit toolchain.)

### OpenSSL and libcurl

`src/CMakeLists.txt` does `find_package(OpenSSL REQUIRED COMPONENTS Crypto)` and
`find_package(CURL REQUIRED)` for **every** target, and Debian/Ubuntu ship no
MinGW build of either. Without them the configure step fails with
`OPENSSL_INCLUDE_DIR-NOTFOUND`. Build both into the same prefix as turbojpeg:

```bash
# OpenSSL (static; installs into <prefix>/lib64)
curl -sLO https://github.com/openssl/openssl/releases/download/openssl-3.5.4/openssl-3.5.4.tar.gz
tar xzf openssl-3.5.4.tar.gz -C _deps
(cd _deps/openssl-3.5.4 && ./Configure mingw64 \
  --cross-compile-prefix=x86_64-w64-mingw32- no-shared no-tests no-docs \
  --prefix="$PWD/../mingw-x86_64" --openssldir="$PWD/../mingw-x86_64/ssl" \
  && make -j"$(nproc)" && make install_sw)

# libcurl (static, Schannel TLS)
curl -sLO https://curl.se/download/curl-8.11.1.tar.gz
tar xzf curl-8.11.1.tar.gz -C _deps
cmake -S _deps/curl-8.11.1 -B _deps/build-curl -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="$PWD/cmake/toolchains/mingw-w64-x86_64.cmake" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PWD/_deps/mingw-x86_64" \
  -DBUILD_SHARED_LIBS=OFF -DBUILD_STATIC_LIBS=ON \
  -DCURL_USE_SCHANNEL=ON -DCURL_USE_OPENSSL=OFF \
  -DCURL_USE_LIBPSL=OFF -DBUILD_CURL_EXE=OFF -DBUILD_TESTING=OFF \
  -DCURL_DISABLE_LDAP=ON -DCURL_USE_LIBSSH2=OFF -DUSE_NGHTTP2=OFF -DCURL_ZLIB=OFF
cmake --build _deps/build-curl -j"$(nproc)" && cmake --install _deps/build-curl
```

curl uses **Schannel** (the Windows certificate store) rather than OpenSSL, so
there is no CA bundle to ship next to `Main.exe`. Building curl with CMake
rather than autotools matters: it installs a `CURLConfig.cmake` that carries
`CURL_STATICLIB` on the imported target, which the autotools build does not.

## Configure and build

```bash
cmake -S . -B build-mingw -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/mingw-w64-x86_64.cmake \
  -DCMAKE_BUILD_TYPE=Release \
  -DENABLE_EDITOR=ON \
  -DMU_TURBOJPEG_STATIC_LIB="$PWD/_deps/mingw-x86_64/lib/libturbojpeg.a" \
  -DCMAKE_FIND_ROOT_PATH="$PWD/_deps/mingw-x86_64" \
  -DCMAKE_PREFIX_PATH="$PWD/_deps/mingw-x86_64" \
  -DOPENSSL_ROOT_DIR="$PWD/_deps/mingw-x86_64"

cmake --build build-mingw -j"$(nproc)"
```

The three path variables are required, not optional: the toolchain sets
`CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY`, so `find_package` searches *only*
under the root paths and will not see the prefix above unless it is listed.

Use `cmake/toolchains/mingw-w64-i686.cmake` and the i686 turbojpeg path for a
32-bit build.

A correct build links everything statically — `objdump -p build-mingw/src/Main.exe
| grep 'DLL Name'` should list only Windows OS DLLs, with no `libgcc_s_seh-1`,
`libstdc++-6`, `libcurl`, `libcrypto` or `libturbojpeg` among them.

## The network library (`.dll`)

Native AOT cannot cross-OS compile: a **Linux** `dotnet` cannot produce the
Windows `MUnique.Client.Library.dll`. Two options:

- **With Windows `dotnet.exe` via WSL interop** (`/mnt/c/Program
  Files/dotnet/dotnet.exe` on `PATH`): CMake uses it to build the real `.dll`,
  giving a connectable client.
- **With only native Linux `dotnet`:** the client compiles and links but the
  `.dll` is skipped (CMake warns); it runs but cannot connect. Build the Windows
  client on Windows, or use the [native Linux client](../linux/console.md), for
  online play.

### Copying the `.dll` in by hand

If you build the library separately (on Windows, a VM, or from a CI artifact),
note that a `PublishAot` publish emits **two files with the same name**:

```
publish/MUnique.Client.Library.dll          managed IL stub,  ~128 KB   <- WRONG
publish/native/MUnique.Client.Library.dll   Native AOT binary, ~3 MB+   <- copy this
```

Copying the top-level one gives a client that starts, loads the library, and
then fails with **"Network library loaded but function
`ConnectionManager_SetLogCallback` not found. Version mismatch."** —
[`Connection.cpp`](../../../src/source/Dotnet/Connection.cpp) resolves the
entry points with `GetProcAddress`, and the managed stub exports none of them.

Check before copying — the right file is megabytes, not kilobytes, and has
~216 exports:

```bash
objdump -p MUnique.Client.Library.dll | grep -c 'Export RVA'
```

## Run

```bash
cd build-mingw/src && ./Main.exe
```

(Runs under WSLg, or copy the `build-mingw/src` directory to Windows.)

## Tests

Install `wine` (and `wine32` for 32-bit), then:

```bash
cmake -S . -B build-mingw -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/mingw-w64-x86_64.cmake \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON \
  -DMU_TURBOJPEG_STATIC_LIB="$PWD/_deps/mingw-x86_64/lib/libturbojpeg.a"
cmake --build build-mingw -j"$(nproc)"
ctest --test-dir build-mingw --output-on-failure
```
