# PromptVault Team Edition 👥

ChatGPT, Google Gemini, Claude ve LLM modelleri için ortak ekip prompt kasası; **Gemini Gems**, **Custom GPTs**, **Claude Skills** yönetim merkezi ve **Google Workspace Ortak Drive** ile anında (gerçek zamanlı) senkronizasyon motoru.

---

## 🔑 Google Cloud Service Account JSON Alma Rehberi (%100 Ücretsiz)

PromptVault Team, sunucularda veya ekip bilgisayarlarında kullanıcı girişi gerektirmeden arka planda şirket Ortak Drive'ına yedekleme yapmak için **Google Cloud Hizmet Hesabı (Service Account)** kullanır.

Bu işlem tamamen **ücretsizdir**, kredi kartı gerektirmez.

### Adım Adım Kurulum:

1. **Google Cloud Console'a Giriş Yapın:**
   - [console.cloud.google.com](https://console.cloud.google.com) adresine gidin ve Google hesabınızla giriş yapın.
2. **Yeni Proje Oluşturun:**
   - Üst bardaki proje seçiciye tıklayın ve **"Yeni Proje" (New Project)** butonuna basın.
   - Proje adına örneğin `PromptVault-Team` yazıp **"Oluştur"**a tıklayın.
3. **Google Drive API'yi Etkinleştirin:**
   - Sol menüden **"API'ler ve Hizmetler" -> "Kitaplık" (Library)** sekmesine gidin.
   - Arama çubuğuna **"Google Drive API"** yazıp çıkan sonuca tıklayın ve **"Etkinleştir" (Enable)** butonuna basın.
4. **Hizmet Hesabı (Service Account) Oluşturun:**
   - Sol menüden **"API'ler ve Hizmetler" -> "Kimlik Bilgileri" (Credentials)** sekmesine gelin.
   - Üstteki **"+ Kimlik Bilgileri Oluştur" -> "Hizmet Hesabı" (Service Account)** seçeneğini seçin.
   - Hizmet hesabı adına `promptvault-bot` yazıp **"Oluştur ve Devam Et"** -> ardından **"Bitti"**ye tıklayın.
5. **JSON Anahtarını İndirin:**
   - Oluşturduğunuz hizmet hesabının e-posta adresine (örn. `promptvault-bot@promptvault-team.iam.gserviceaccount.com`) tıklayın.
   - Üstteki **"Anahtarlar" (Keys)** sekmesine geçin.
   - **"Anahtar Ekle" (Add Key) -> "Yeni anahtar oluştur" -> "JSON"** seçeneğini seçip **"Oluştur"**a basın.
   - Bilgisayarınıza otomatik olarak bir `.json` dosyası inecektir.
6. **Şirket Drive'ına Yetki Verin:**
   - İndirdiğiniz JSON dosyasındaki `client_email` adresini kopyalayın.
   - Google Drive'ınızdaki **Ortak Drive'a (Shared Drive)** veya yedeklemek istediğiniz klasöre gidin.
   - **"Paylaş"** (veya Üye Ekle) diyerek kopyaladığınız bot e-posta adresini yapıştırın ve yetkisini **"İçerik Yöneticisi" (Content Manager)** olarak ayarlayın.
7. **PromptVault Team'e Yükleyin:**
   - İndirdiğiniz `.json` dosyasını `~/.promptvault_team/gdrive_team/service_account.json` olarak kaydedin veya PromptVault Team API'si (`/api/team/gdrive/upload-service-account`) üzerinden yükleyin.

---

## 🚀 Başlatma

Klasör içindeki **`start_team.bat`** dosyasına çift tıklayarak Team sunucusunu doğrudan çalıştırabilirsiniz (Port 7861).

Terminalden başlatmak için:
```powershell
.\.venv\Scripts\promptvault team --port 7861 --open
```

---

## 🌟 Temel Yetenekler

1. **LLM Platform Bağımsızlığı:**
   - Model versiyonlarına kilitlenmez: `ChatGPT`, `Gemini`, `Claude`, `Reasoning`, `Açık Kaynak`.
2. **Gems, Custom GPTs & Claude Skills:**
   - Sistem yönergeleri, roller ve dinamik `{{degisken}}` şablonları.
3. **Anında Senkronizasyon (Real-Time Sync):**
   - Kasaya bir prompt veya Gem eklendiği anda arka planda otomatik Google Drive'a işlenir.
4. **API Üretim Hazırlığı:**
   - `/api/team/execute/render` ve `/api/team/execute/preview` ile sonraki fazda doğrudan API üzerinden sohbet ve görsel üretim altyapısı.
