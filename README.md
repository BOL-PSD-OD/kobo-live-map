# kobo-live-map — แผนที่สำรวจอัปเดตอัตโนมัติจาก KoboToolbox

แผนที่ร้านค้ารับชำระ (Alipay / Alipay+ / WeChat Pay) แขวงหลวงพระบาง
สร้างใหม่อัตโนมัติทุก 30 นาทีด้วย GitHub Actions → เผยแพร่บน GitHub Pages
ข้อมูลและป้ายคำถามภาษาลาวดึงสดจาก Kobo API — **API Token เก็บใน GitHub Secrets ไม่อยู่ในโค้ด**

## โครงสร้าง

| ไฟล์ | หน้าที่ |
|---|---|
| `build_map.py` | ดึงข้อมูล + ฟอร์มจาก Kobo API แล้วสร้าง `site/index.html` (ใช้แค่ Python ล้วน ไม่ต้องลงไลบรารี) |
| `template.html` | แม่แบบแผนที่ Leaflet (พื้นหลัง 3 แบบ, ค้นหาร้าน, การ์ดรายละเอียด, ตัวกรองสถานะ+ประเภท) |
| `assets/districts_lpb.geojson` | ขอบเขต 12 เมืองของหลวงพระบาง (EPSG:4326, สร้างจาก shapefile ครั้งเดียว) |
| `.github/workflows/build-map.yml` | ตารางรันอัตโนมัติ + เผยแพร่ขึ้น Pages |

## วิธีติดตั้ง (ทำครั้งเดียว ~10 นาที)

### 1. สร้าง repo และอัปโหลดโค้ด
```bash
cd kobo-live-map
git init
git add .
git commit -m "kobo live map"
git branch -M main
git remote add origin https://github.com/<ชื่อบัญชี>/<ชื่อrepo>.git
git push -u origin main
```
> ⚠️ GitHub Pages ฟรีใช้ได้กับ **repo สาธารณะ** เท่านั้น — โค้ดในโฟลเดอร์นี้ไม่มีความลับใด ๆ
> แต่**หน้าแผนที่จะเปิดได้โดยไม่ต้องล็อกอิน** (URL เดายาก แต่ไม่ใช่ระบบล็อกอิน)
> ถ้าข้อมูลร้านค้าห้ามเปิดเผย ให้ใช้ repo ส่วนตัว + GitHub Pro หรือคุยกันเรื่อง Hugging Face Private Space แทน

### 2. ใส่ความลับ (Secrets)
ใน repo: **Settings → Secrets and variables → Actions → New repository secret** สร้าง 2 ตัว:

| ชื่อ Secret | ค่า | หาได้จาก |
|---|---|---|
| `KOBO_TOKEN` | API token | เข้า KoboToolbox → คลิกรูปโปรไฟล์ → Account Settings → **Security → API Key** (หรือเปิด `https://kf.kobotoolbox.org/token/?format=json`) |
| `KOBO_ASSET_UID` | รหัสฟอร์ม เช่น `aB3dE5fG7hJ9kLmN0pQr` | เปิดฟอร์มใน Kobo แล้วดูใน URL: `.../forms/`**`aB3dE5...`**`/landing` |
| `MAP_PASSWORD` | รหัสผ่านหน้าแผนที่ | ตั้งเอง — **ตัวอักษร+ตัวเลข+อักขระพิเศษ ยาว 10 ตัวขึ้นไป** เช่น `LPB@map2026!kobo` |

> 🔒 เมื่อตั้ง `MAP_PASSWORD` หน้าแผนที่ทั้งหน้าจะถูกเข้ารหัส AES-256 — คนเปิดลิงก์ต้องใส่รหัสก่อนถึงเห็นข้อมูล
> ถ้าไม่ตั้ง ระบบจะเผยแพร่แบบไม่มีรหัส (มีคำเตือนใน log)

### การหมุนรหัสผ่านทุก 3 เดือน
1. คิดรหัสใหม่ (คุณเป็นคนตั้งเอง จึงรู้รหัสเสมอ)
2. Settings → Secrets → แก้ค่า `MAP_PASSWORD` ทับของเดิม
3. Actions → Build survey map → **Run workflow** หนึ่งครั้ง
4. แจ้งรหัสใหม่ให้ทีมผ่านช่องทางภายใน — รหัสเก่าและเครื่องที่จำรหัสเก่าไว้จะหลุดทันที

ถ้าบัญชีอยู่เซิร์ฟเวอร์อื่น (เช่น `eu.kobotoolbox.org`) ให้เพิ่ม **Variable** (แท็บ Variables) ชื่อ `KOBO_SERVER` ค่า `https://eu.kobotoolbox.org`

### 3. เปิด GitHub Pages
**Settings → Pages → Build and deployment → Source = "GitHub Actions"**

### 4. รันครั้งแรก
แท็บ **Actions → Build survey map → Run workflow** → รอ ~1 นาที
แผนที่จะอยู่ที่ `https://<ชื่อบัญชี>.github.io/<ชื่อrepo>/`

จากนั้นระบบรันเองทุก 30 นาที (แก้ความถี่ได้ที่บรรทัด `cron` ใน `.github/workflows/build-map.yml`)

## ทดสอบในเครื่อง (ไม่ต้องมี token)
```bash
python build_map.py --fake fake.json
# fake.json = {"form": <asset json>, "records": [<submission>, ...]}
```
หรือทดสอบกับข้อมูลจริง:
```bash
set KOBO_TOKEN=xxxx
set KOBO_ASSET_UID=aXXXX
python build_map.py
```
ผลลัพธ์อยู่ที่ `site/index.html`

## อัปเดตต่อ submission ทันที (ทางเลือก ขั้นสูง)
Kobo REST Services ยิงข้อมูลออกได้ทุกครั้งที่มีคนส่งฟอร์ม แต่ body ของมันไม่ตรงกับที่
GitHub `repository_dispatch` ต้องการ จึงต้องมีตัวกลางเล็ก ๆ (เช่น Cloudflare Worker ฟรี)
รับ webhook จาก Kobo แล้ว POST ไป GitHub — workflow รองรับ event `kobo-submission` ไว้ให้แล้ว
ถ้าตารางทุก 30 นาทียังไม่พอค่อยทำส่วนนี้เพิ่ม
