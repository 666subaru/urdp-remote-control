# RDP kodekleri ve H.265 meselesi

Kısa cevap: **RDP'de H.265 (HEVC) yoktur.** Tavan H.264'tür (AVC444).
Bu bir FreeRDP eksikliği değil, protokolün kendisinde öyle.

## Kanıt

### 1. Protokolün kodek listesi

RDP grafik hattının (MS-RDPEGFX) tanıdığı bütün kodekler, FreeRDP 3.31.1'in
başlık dosyasında (`/usr/include/freerdp3/freerdp/channels/rdpgfx.h`):

```c
RDPGFX_CODECID_UNCOMPRESSED   = 0x0000
RDPGFX_CODECID_AV1            = 0x0001   // #if defined(WITH_GFX_AV1)
RDPGFX_CODECID_CAVIDEO        = 0x0003
RDPGFX_CODECID_CLEARCODEC     = 0x0008
RDPGFX_CODECID_CAPROGRESSIVE  = 0x0009
RDPGFX_CODECID_PLANAR         = 0x000A
RDPGFX_CODECID_AVC420         = 0x000B
RDPGFX_CODECID_ALPHA          = 0x000C
RDPGFX_CODECID_CAPROGRESSIVE_V2 = 0x000D
RDPGFX_CODECID_AVC444         = 0x000E
RDPGFX_CODECID_AVC444v2       = 0x000F
```

Listede HEVC/H.265 diye bir kimlik yok. En yenisi AVC444v2 ve o da H.264.

### 2. Komut satırının kabul ettiği değerler

`xfreerdp3 /?` çıktısındaki `/gfx` seçeneği yalnızca şunları listeler:
`progressive`, `RFX`, `AVC420`, `AVC444`.

Kütüphane ikilisinde ayrıştırıcının karşılaştırdığı dizeler de aynı:

```
$ strings /usr/lib/libfreerdp-client3.so.3.31.1 | grep -xiE "avc444|avc420|progressive|av1|hevc|h265"
AVC420
AVC444
progressive
```

### 3. Yapı bayrakları

```
$ xfreerdp3 /buildconfig | tr ' ' '\n' | grep -E "^WITH_(GFX_H264|GFX_AV1|AOM|DAV1D)="
WITH_GFX_H264=ON
WITH_GFX_AV1=OFF
WITH_AOM=OFF
WITH_DAV1D=OFF
```

AV1 kimliği FreeRDP'nin **kendi eklentisi** ("Custom Extension") ve yalnızca iki
FreeRDP ucu arasında geçerli — Windows bu dili konuşmuyor. Üstelik dağıtım
yapılarında kapalı geliyor.

## Neden "RDP H.265 kullanıyor" diye bir izlenim oluşuyor?

Birkaç ihtimal:

- **AVC444 ile karıştırma.** Windows 10/11'in RDP'si donanım hızlandırmalı H.264
  AVC444 kullanır; Görev Yöneticisi'nde GPU'nun "Video Encode" grafiği çalışır ve
  bu çoğu zaman "modern kodek" olarak yorumlanır.
- **Çoklu ortam yönlendirme.** Uzak makinede oynatılan video, RDP masaüstünü
  kodlayan kodekten bağımsız olarak `/video` kanalıyla aktarılabilir; oradaki
  akış HEVC olabilir. Masaüstünün kendisi yine H.264'tür.
- **RDP olmayan protokoller.** Parsec, Sunshine/Moonlight, NVIDIA/AMD kendi
  akış çözümleri gerçekten HEVC ve AV1 kullanır. RDP kullanmazlar.

## Uygulamanın davranışı

Deneyim sekmesindeki kodek listesi tam olarak FreeRDP'nin kabul ettiği değerleri
içerir, fazlasını değil. Bunun bir nedeni var: **FreeRDP'nin `/gfx` ayrıştırıcısı
tanımadığı değerleri sessizce yok sayıyor.**

```
$ xfreerdp3 /gfx:ZZZZBOGUS /v:0.0.0.0 ...   # hiçbir hata vermez
```

Yani listeye "H.265" diye bir satır koysaydık, seçildiğinde hiçbir şey olmaz,
kullanıcı da bunu anlayamazdı. Onun yerine uygulama açılışta
`xfreerdp3 /buildconfig` çıktısını okuyup gerçekten hangi kodeklerin derlenmiş
olduğunu Deneyim sekmesinde yazar; derlenmemiş olanları da seçilemez yapar.
