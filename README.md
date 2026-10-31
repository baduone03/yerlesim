# LoadPlanner TR

Araç içi yük yerleşim ve lojistik planlama uygulaması. Python + Streamlit.

Kullanıcı palet, koli, sandık vb. yükleri girer; uygulama eldeki araç tiplerinin her biri için
**gerçek 3D yerleşim** yapar, tek tip ve karma filo seçeneklerini (adet ve sefer maliyetiyle)
karşılaştırır, her yükün X/Y/Z koordinatını ve yönelimini, yükleme/boşaltma sırasını, yükleme
metresini (LDM), ihlalleri, sabitleme uyarılarını ve yerleştirilemeyen yükleri nedenleriyle raporlar.

> **Önemli:** Bu sürüm bir karar destek aracıdır. Yük sabitleme, dinamik kuvvetler ve kesin aks
> yükü güvenliği **hesaplanmaz**. Ayrıntı için aşağıdaki "Sınırlar" bölümüne bakın.

## Kurulum ve çalıştırma

Python 3.10+ gerekir.

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Tarayıcıda `http://localhost:8501` açılır. Aynı ağdaki telefondan erişmek için
`streamlit run streamlit_app.py --server.address 0.0.0.0` ile başlatıp bilgisayarın IP adresini kullanın.

Testler:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

### Streamlit Community Cloud'a yükleme

1. https://share.streamlit.io adresine GitHub hesabıyla giriş yapın.
2. **Create app** → **Deploy a public app from GitHub** (veya "Yup, I have an app").
3. Repository: `baduone03/yerlesim`, Branch: uygulamanın bulunduğu dal, Main file path: `streamlit_app.py`.
4. **Deploy**. Bağımlılıklar `requirements.txt` dosyasından kurulur; ayarlar `.streamlit/config.toml` içindedir.

## Kullanım

1. **Araç**: Hazır profilleri inceleyin/düzenleyin. **Kasa tipi** (kapalı / tenteli / frigorifik / özel)
   kapıları belirler: tenteli araçta iki yandan forkliftle erişim vardır, frigorifik araç soğuk zincir
   yükünü taşıyabilir. Gerçek aracın iç ölçülerini, ruhsattaki **istiap haddini**, isteğe bağlı sefer
   maliyetini, varsa aks konumlarını ve yasak bölgeleri (teker davlumbazı vb.) girin. Kamyon + römork
   profilinde römorkun ölçüleri ayrıca girilir.
2. **Yükler**: Formla yük ekleyin (hazır palet tabanları: Euro 120×80, endüstriyel 120×100, yarım
   80×60; telefonda tek sütun), satırları açarak düzenleyin/silin, masaüstünde tablo ile toplu düzenleyin
   veya CSV içe aktarın. Nadir alanlar "Diğer özellikler" altındadır.
3. **Kurallar**: Teslimat sırası / LIFO / FIFO; "Gelişmiş" altında karışık istif, kısmi yükü ortalama,
   asgari taban desteği, kapı önü boşluk; birlikte taşınamaz yük grupları.
4. **Optimizasyon**: Öncelik (minimum araç, minimum maliyet, maksimum hacim, boşaltma kolaylığı) ve
   **kullanılabilir araçlar tablosu** (her tipten kaç adet var, sefer maliyeti). Uygulama tek tip
   seçenekleri ve gerekiyorsa **karma filoyu** (ör. 1 tır + 1 kamyonet) karşılaştırır.
5. **3D Yerleşim**: Plotly 3D model, üstten/yandan/arkadan 2D görünümler, adım adım yükleme
   kaydırıcısı, koordinat tablosu. İhlalli yükler kırmızı vurgulanır.
6. **Rapor**: Seçilen araç ve nedeni, toplam adet/ağırlık/hacim, hacim ve ağırlık kullanım
   oranları, ihlaller, uyarılar, yükleme/boşaltma sırası, yerleştirilemeyen yükler.
7. **Dışa Aktar**: **Yükleme talimatı PDF** (şoför/depo için; kontrol durumu, adım numaralı üstten ve
   yandan çizim, adım adım yükleme listesi, durak bazında boşaltma, özel uyarılar), yerleşim planı +
   koordinatlar CSV, yerleştirilemeyen yükler CSV, yük listesi CSV.

Girdiler (yük listesi, kurallar, değiştirilmiş araç profilleri) sayfa adresine sıkıştırılarak yazılır;
sayfa yenilenince geri yüklenir ve bağlantı paylaşılabilir. Hesaplama sonuçları saklanmaz.

### Elle düzenleme

3D Yerleşim sekmesindeki "Yerleşimi elle düzenle" bölümünden bir yük taşınabilir/döndürülebilir, iki yük
yer değiştirebilir, yük araçtan çıkarılabilir veya yerleşmeyen bir yük eklenebilir ("kurallara uygun ilk
konum" önerisiyle ya da koordinat girerek). Her değişiklikten sonra plan baştan doğrulanır; hatalı
değişiklik engellenmez, ihlal olarak gösterilir. "Otomatik plana geri dön" ile değişiklikler geri alınır.

### Birlikte taşınamaz yükler (ayrım grupları)

Yüklere "ayrım grubu" (ör. `gıda`, `kimyasal`) verilir; Kurallar sekmesinde uyumsuz çiftler
(`gıda, kimyasal`) ve aralarındaki asgari yatay mesafe tanımlanır. Uyumsuz yükler hiçbir zaman üst üste
konmaz ve aralarındaki yatay (XY) mesafe bu değerden az olamaz. 0 cm'de yan yana temas serbesttir.
Bu kural bir mevzuat (ör. ADR) uyumluluk kontrolü değildir; ayrım ihtiyacını kullanıcı tanımlar.

Örnek veri: `examples/ornek_yukler.csv` (arayüzde "Örnek yük listesini yükle").

### CSV biçimi

Ayırıcı `;` veya `,`; ondalık virgül desteklenir; UTF-8 veya Windows-1254 kodlama.
Zorunlu sütunlar: `kod, adet, boy_cm, en_cm, yukseklik_cm, birim_agirlik_kg`.
İsteğe bağlı: `ad, tip (palet/koli/sandik/makine/rulo/ozel), palet_dahil, ustune_yuk_konabilir,
ust_azami_yuk_kg, dondurulebilir, yan_yatirilabilir, kirilabilir, istiflenebilir, teslimat_sirasi,
yukleme_onceligi, notlar, ayrim_grubu, soguk_zincir, gonderici`. Evet/hayır alanları `evet/hayır`, `1/0`, `true/false` kabul eder.
Hatalı satırlar satır numarasıyla raporlanır.

## Koordinat sistemi

- **X**: araç boyunca; `X=0` kasanın ön duvarı (kabin), `X=L` arka kapı.
- **Y**: genişlik; `Y=0` sol duvar.
- **Z**: tabandan yükseklik.
- Koordinat, yükün en küçük köşesidir; ölçüler cm, ağırlıklar kg.

## Algoritma

`loadplanner/optimizer.py` — extreme-point tabanlı sezgisel 3D yerleşim:

1. Her yük için **boşaltma sırası (rank)** hesaplanır: teslimat modunda teslimat durağı,
   LIFO'da ters yükleme önceliği, FIFO'da yükleme önceliği.
2. En geç boşaltılacak yükler önce yerleştirilir (kasanın önüne). Aynı grup içinde
   üzerine yük konulabilenler ve ağır/büyük olanlar önce gelir.
3. Aday noktalar öndeki/alttaki konum önce gelecek şekilde denenir. Her aday için **sert
   kısıtlar**: kasa sınırı, çakışma, yasak bölge, kapı açıklığı, taban desteği (varsayılan %75
   ve ağırlık merkezi destek alanı içinde), istiflenebilirlik / kırılabilirlik / üstüne yük
   konamaz, üst yük dayanımı (yük aşağıya doğru temas alanı payıyla aktarılır), karışık durak
   istifi, kalan ağırlık kapasitesi ve **kapı erişimi** (yükün kapıya giden düz koridorunda
   kurala aykırı yük olmaması; birden fazla kapı varsa herhangi biri yeterli).
4. Birkaç sıralama/yönelim/doldurma stratejisi denenir (kabinden kapıya duvar örme ve boyuna
   şeritler), en iyi plan seçilir. Strateji setleri 6 yük listesi × 4 araç üzerinde 16 stratejinin
   tamamıyla karşılaştırılarak seçilmiştir. Sığmayanlar sonraki araca aktarılır.
5. Araç karşılaştırmasında önce bütün tipler hızlı setle taranır, en iyi 3 seçenek tam setle yeniden
   planlanır; tek tip yetmiyorsa eldeki araçlarla karma filo denenir.
6. Sonuç `loadplanner/constraints.py::verify_plan` ile **algoritmadan bağımsız** olarak yeniden
   doğrulanır: bütün geometrik ve istif kontrolleri baştan hesaplanır, boşaltma sırası
   simüle edilir. Bir yükü çıkarmak için önce başka bir yükün indirilmesi gerekiyorsa
   **ERİŞİM** ihlali yazılır. Yükleme sırası, geçerli boşaltma sırasının tersidir.

Algoritma optimum çözümü bulduğunu **iddia etmez**.

### FIFO / LIFO / teslimat sırası

- **Teslimat sırası** (önerilen): 1. durağın yükü kapıya en yakın, yükleme sırası tersinden.
- **LIFO**: arka kapılı aracın doğal düzeni; son yüklenen ilk çıkar.
- **FIFO**: ilk yüklenen ilk çıkar. Tek arka kapılı araçta ilk yüklenen en içte kalacağından,
  farklı öncelikteki yükler ancak birbirinin önüne/üstüne gelmezse (yan yana şeritler) mümkündür.
  Mümkün değilse ilgili yükler "erişim kuralı nedeniyle" yerleştirilemez olarak raporlanır.
- FIFO/LIFO seçiliyken teslimat durakları bu sıraya aykırıysa **kural çelişkisi** uyarısı verilir.

### Yerleştirilemeyen yükler

Her yerleşmeyen yük kapsamıyla raporlanır:
- **Tek başına sığmıyor**: ölçü (hiçbir izinli yönde), kapı açıklığı veya birim ağırlık aracı aşıyor.
- **Birlikte sığmıyor**: yük tek başına sığar ama liste birlikte sığmıyor; neden ayrıca
  belirtilir (ağırlık kapasitesi doldu / yer kalmadı / yalnız erişim kuralına aykırı yer var /
  yalnız istif kuralına aykırı yer var).

## Araç profilleri

`loadplanner/vehicle_data.py` (hepsi **DEMO/temsili değer**, üretici verisi değildir):

| Profil | Kasa / erişim |
|---|---|
| Panelvan | arka + sağ sürgülü kapı, teker davlumbazları |
| Orta boy / büyük kapalı kasa kamyonet | arka kapı |
| 10 teker kamyon | kapalı kasa, arka kapı |
| Kırkayak (12 teker) | tenteli: arka + iki yan |
| Tenteli dorse 13,60 m | arka + iki yan |
| Mega tenteli dorse 13,60 m | iç yükseklik ~3 m, arka + iki yan |
| Kapalı (kutu) dorse 13,60 m | arka kapı |
| Frigorifik dorse 13,40 m | arka kapı, soğuk zincir |
| Kamyon + römork (2 × 7,7 m) | iki kasa tek araç; kamyon kasası yalnız yandan, römork arka + yan |
| 20' DC, 40' DC, 40' HC, 45' HC konteyner | arka kapı (DC 234×228, HC 234×258 cm açıklık); dara ve azami brüt ile |
| 20' / 40' HC reefer konteyner | arka kapı, soğuk zincir |
| Özel araç | kullanıcı tanımlı |

Gerçek kullanımda aracın iç ölçüleri, ruhsattaki istiap haddi ve aks bilgisi girilmelidir. Demo
profillerde aks yük limitleri ve sefer maliyetleri boştur.

## Konteyner taşımacılığı (FCL / LCL)

Optimizasyon sekmesinin başındaki **Taşıma şekli** seçimiyle karayolu araçları ile konteynerler ayrı
değerlendirilir (araç sekmesinde de "Göster: Konteynerler").

- **FCL (komple):** yükler için en uygun konteyner tipi ve adedi seçilir; konteyner başı navlun girilirse
  "Minimum maliyet" ile karşılaştırılır, gerekirse farklı tipler birlikte (ör. 1 × 40' HC + 1 × 20' DC)
  önerilir. Soğuk zincir yükü yoksa reefer konteyner önerilmez.
- **LCL (parsiyel / konsolidasyon):** yüklere **Gönderici** girilir (LCL seçiliyken formda ana alandadır;
  CSV sütunu `gonderici`). LCL'ye geçince "Aynı göndericinin yükleri bir arada" kuralı açılır: farklı
  göndericilerin yükleri üst üste konmaz, aynı göndericininkiler arka arkaya yüklenir. Göndericiye göre
  adet, ağırlık, m³, **W/M = max(m³, ton)** (asgari W/M ile) ve ücret dökümü verilir; birden fazla
  konteynere bölünen gönderici bildirilir.
- **VGM ön hesabı:** her konteyner için dara + yük = brüt ağırlık ve CSC azami brüt ile karşılaştırma.
  İstif/sabitleme malzemesi dahil değildir; resmi VGM SOLAS'a göre tartımla veya Yöntem 2 ile beyan edilir.
- Rapor, PDF (VGM satırı, LCL dökümü) ve CSV (LCL dökümü, VGM) çıktılarında yer alır.

## Lojistik kontroller

- **İstif dayanımı:** "Üzerine konulabilecek azami ağırlık" girilmezse yükün **kendi ağırlığı kadar**
  kabul edilir: aynı yükten biri üstüne konabilir, daha ağırı (ör. palet koli üstüne) konamaz.
- **Soğuk zincir:** işaretli yükler yalnız frigorifik araca yüklenir.
- **Taşan yük:** bir yük, başka bir yükün taşan kısmının altına sokulmaz (üstteki yük ona temas edip
  ezebilir).
- **LDM (yükleme metresi):** kaplanan taban alanı / 2,40 m.
- **Bölünen durak:** bir teslimat durağının yükü birden fazla araca bölünürse bildirilir; araç sayısı
  artmadan bölünmeyen bir plan bulunabiliyorsa o tercih edilir.
- **Ağırlık dağılımı:** yanal kayma, yüksek ağırlık merkezi ve kısmi ağır yükte ağırlık merkezinin
  öne/arkaya kayması uyarılır. "Kısmi yükü kasa ortasına kaydır" kuralı bütün yükü kapıya doğru
  kaydırır (önde kalan boşluk takozlanmalıdır).
- **Sabitleme uyarıları:** tabanda 15 cm'den büyük yanal/ön boşluklar, kapı tarafındaki boşluk ve önü/yanı
  açık üst kat yükler raporlanır. Bu bir sabitleme hesabı değildir; nereye dolgu/spanzet gerektiğini gösterir.

## Sınırlar

- Yük sabitleme (spanzet, kilit, sürtünme), fren/viraj/dinamik kuvvetler **hesaplanmaz**.
- Aks yükü, yalnızca aks konumları girildiyse iki destekli basit kiriş modeliyle ve yalnız faydalı
  yük için **yaklaşık** hesaplanır; aks başına izin verilen faydalı yük girilmediyse uygunluk
  değerlendirilmez. Boş araç ağırlık dağılımı modele dahil değildir.
- Rulo ve özel şekilli yükler çevreleyen dikdörtgen kutu olarak modellenir.
- Kapı erişimi düz koridor modeliyle kontrol edilir (yük kapıya doğru düz çekilir); kapı açıklığı
  dar ise yükün yana kaydırılarak çıkarılması varsayılır, yalnız açıklık ölçüsü kontrol edilir.
- Karma filo seçimi sezgiseldir (her adımda kalan yükü en uygun taşıyan aracı seçer); en ucuz
  kombinasyonun bulunduğu garanti edilmez. Rota/mesafe planlaması yapılmaz.
- Büyük listelerde (250 adet civarı, 10 araç tipi) hesaplama yaklaşık yarım dakika sürer.

## Proje yapısı

```
streamlit_app.py            Streamlit arayüzü (7 bölüm)
loadplanner/
  models.py                 Veri modelleri (araç, kapı, bölge, aks, yük, yerleşim, plan)
  vehicle_data.py           DEMO araç profilleri
  validation.py             Girdi doğrulamaları (Türkçe mesajlar)
  optimizer.py              Sezgisel 3D yerleşim, filo planlama, araç karşılaştırma
  constraints.py            Geometri, sıra kuralları, bağımsız doğrulama, ağırlık analizi
  visualization.py          Plotly 3D ve 2D görünümler
  io_csv.py                 CSV içe/dışa aktarma
  manual.py                 Elle düzenleme işlemleri ve yeniden doğrulama
  pdf_report.py             Yükleme talimatı PDF'i (fpdf2)
  state_codec.py            Girdilerin sayfa adresinde saklanması
  container.py              VGM ön hesabı ve LCL gönderici (W/M) dökümü
assets/fonts/               PDF için DejaVu Sans (Türkçe karakter desteği; lisansı klasörde)
examples/ornek_yukler.csv   Örnek yük listesi
tests/                      pytest testleri (algoritma, lojistik kurallar, CSV, PDF, arayüz)
```

## Sonraki adımlar (öneri)

- Gerçek araç verileriyle aks yükü (boş araç dağılımı dahil) ve kalıcı araç/plan kaydı
- Rota ve teslimat noktalarının araçlara bölünmesi (mesafe/zaman)
- EN 12195-1 tabanlı sabitleme hesabı (spanzet sayısı)
- Daha güçlü arama (ör. yerel arama), silindirik yük modeli
