# RDP codecs, and the H.265 question

Short answer: **RDP has no H.265 (HEVC).** The ceiling is H.264 (AVC444). This
is not a FreeRDP shortcoming — the protocol itself does not define it.

🇹🇷 [Türkçe](codecs-tr.md)

## Evidence

### 1. The protocol's codec list

Every codec the RDP graphics pipeline (MS-RDPEGFX) knows about, from FreeRDP
3.31.1's header (`/usr/include/freerdp3/freerdp/channels/rdpgfx.h`):

```c
RDPGFX_CODECID_UNCOMPRESSED     = 0x0000
RDPGFX_CODECID_AV1              = 0x0001   // #if defined(WITH_GFX_AV1)
RDPGFX_CODECID_CAVIDEO          = 0x0003
RDPGFX_CODECID_CLEARCODEC       = 0x0008
RDPGFX_CODECID_CAPROGRESSIVE    = 0x0009
RDPGFX_CODECID_PLANAR           = 0x000A
RDPGFX_CODECID_AVC420           = 0x000B
RDPGFX_CODECID_ALPHA            = 0x000C
RDPGFX_CODECID_CAPROGRESSIVE_V2 = 0x000D
RDPGFX_CODECID_AVC444           = 0x000E
RDPGFX_CODECID_AVC444v2         = 0x000F
```

There is no HEVC/H.265 id. The newest entry is AVC444v2, and that is still
H.264.

### 2. What the command line accepts

The `/gfx` option in `xfreerdp3 /?` lists only `progressive`, `RFX`, `AVC420`
and `AVC444`. The strings the parser compares against, in the library binary,
are the same:

```
$ strings /usr/lib/libfreerdp-client3.so.3.31.1 | grep -xiE "avc444|avc420|progressive|av1|hevc|h265"
AVC420
AVC444
progressive
```

### 3. Build flags

```
$ xfreerdp3 /buildconfig | tr ' ' '\n' | grep -E "^WITH_(GFX_H264|GFX_AV1|AOM|DAV1D)="
WITH_GFX_H264=ON
WITH_GFX_AV1=OFF
WITH_AOM=OFF
WITH_DAV1D=OFF
```

The AV1 id is FreeRDP's **own extension** ("Custom Extension"), valid only
between two FreeRDP peers — Windows does not speak it — and distribution builds
ship with it off.

## So why does it look like "RDP uses H.265"?

A few possibilities:

- **Confusion with AVC444.** Windows 10/11 RDP uses hardware-accelerated H.264
  AVC444; Task Manager's GPU "Video Encode" graph lights up, which reads as "a
  modern codec".
- **Multimedia redirection.** Video played on the remote machine can be carried
  over the `/video` channel independently of the codec encoding the desktop,
  and that stream may well be HEVC. The desktop itself is still H.264.
- **Non-RDP protocols.** Parsec, Sunshine/Moonlight and the vendor streaming
  stacks really do use HEVC and AV1. They are not RDP.

## What the application does about it

The codec list on the Experience tab contains exactly the values FreeRDP
accepts, and nothing more. There is a reason for that: **FreeRDP's `/gfx`
parser silently ignores values it does not know.**

```
$ xfreerdp3 /gfx:ZZZZBOGUS /v:0.0.0.0 ...   # no error whatsoever
```

So an "H.265" entry in the list would do nothing when selected, and the user
would have no way to tell. Instead the application reads
`xfreerdp3 /buildconfig` at startup, states on the Experience tab which codecs
this build really supports, and disables the ones that were not compiled in.
