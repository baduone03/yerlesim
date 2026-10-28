# LoadPlanner TR

Araç içi yük yerleşim ve lojistik planlama uygulaması (MVP). Python + Streamlit.

Kullanıcı palet, koli, sandık vb. yükleri girer; uygulama seçilen araç profillerinin her biri için
**gerçek 3D yerleşim** yapar, araçları karşılaştırır, her yükün X/Y/Z koordinatını ve yönelimini,
yükleme/boşaltma sırasını, ihlalleri ve yerleştirilemeyen yükleri nedenleriyle raporlar.

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

1. **Araç**: Hazır profilleri inceleyin/düzenleyin. Gerçek aracın iç ölçülerini, ruhsattaki azami
   yükü, kapıları (arka / sağ-sol yan kapı ve açıklıkları), varsa aks konumlarını ve yasak bölgeleri
   (teker davlumbazı, sabitleme alanı) girin. "Özel araç" profili tamamen kullanıcı tanımlıdır.
2. **Yükler**: Formla yük ekleyin (telefonda kolay kullanım için tek sütuna düşer), satırları
   açarak düzenleyin/silin, masaüstünde tablo ile toplu düzenleyin veya CSV içe aktarın.
3. **Kurallar**: Teslimat sırası / LIFO / FIFO, karışık istif izni, asgari taban desteği,
   arka kapı önü boşluk.
4. **Optimizasyon**: Hedef (minimum araç, maksimum hacim kullanımı, boşaltma kolaylığı), araç
   tipleri, tip başına en fazla araç sayısı ve arama derinliği seçilip hesaplanır.
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
yukleme_onceligi, notlar, ayrim_grubu`. Evet/hayır alanları `evet/hayır`, `1/0`, `true/false` kabul eder.
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
4. Birkaç sıralama/yönelim/doldurma stratejisi denenir (duvar örme; FIFO'da boyuna şeritler
   de), en iyi plan seçilir. Sığmayanlar aynı tipten bir sonraki araca aktarılır.
5. Sonuç `loadplanner/constraints.py::verify_plan` ile **algoritmadan bağımsız** olarak yeniden
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

`loadplanner/vehicle_data.py`: Panelvan, orta boy ve büyük kapalı kasa kamyonet, 10 teker kamyon,
13,60 m yarı römork ve özel araç. **Tüm hazır değerler DEMO/temsili değerlerdir**; üretici verisi
değildir. Gerçek kullanımda aracın iç ölçüleri, ruhsattaki / teknik belgedeki kapasite ve aks
bilgisi girilmelidir. Demo profillerde aks yük limitleri boştur.

## Sınırlar (MVP)

- Yük sabitleme (spanzet, kilit, sürtünme), fren/viraj/dinamik kuvvetler **hesaplanmaz**.
- Aks yükü, yalnızca aks konumları girildiyse iki destekli basit kiriş modeliyle ve yalnız faydalı
  yük için **yaklaşık** hesaplanır; aks başına izin verilen faydalı yük girilmediyse uygunluk
  değerlendirilmez. Boş araç ağırlık dağılımı modele dahil değildir.
- Ağırlık merkezi için yalnız yanal kayma ve yüksek ağırlık merkezi **uyarısı** verilir.
- İstif dayanımı yalnız girilen "üzerine konulabilecek azami ağırlık" değerine göre kontrol edilir;
  değer girilmezse sınırsız kabul edilir ve uyarı verilir.
- Rulo ve özel şekilli yükler çevreleyen dikdörtgen kutu olarak modellenir.
- Kapı erişimi düz koridor modeliyle kontrol edilir (yük kapıya doğru düz çekilir); kapı açıklığı
  dar ise yükün yana kaydırılarak çıkarılması varsayılır, yalnız açıklık ölçüsü kontrol edilir.
- Karma filo (farklı tiplerde araçların birlikte kullanımı) desteklenmez; her araç tipi kendi
  başına (gerekirse birden fazla adet) değerlendirilir.
- Büyük listelerde (birkaç yüz adet) hesaplama birkaç on saniye sürebilir.

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
assets/fonts/               PDF için DejaVu Sans (Türkçe karakter desteği; lisansı klasörde)
examples/ornek_yukler.csv   Örnek yük listesi
tests/                      pytest testleri (algoritma, CSV, arayüz duman testi)
```

## Sonraki adımlar (öneri)

- Karma filo ve araçlar arası teslimat durağı bazlı bölme
- Gerçek araç verileriyle aks yükü ve kalıcı araç/plan kaydı
- Yük sabitleme noktaları ve sabitleme önerisi
- Gerçek araç verileriyle aks yükü (boş araç dağılımı dahil)
- Daha güçlü arama (ör. yerel arama / genetik algoritma), silindirik yük modeli
