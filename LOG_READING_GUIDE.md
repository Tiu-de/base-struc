# Huong Dan Doc Va Dien Giai Log

Tai lieu nay mo ta cach doc, doi soat va dien giai log trong he thong ESP32-S3 de phuc vu:
- Kiem thu van hanh.
- Phan tich su co.
- Danh gia hieu qua profile baseline/optimized.

## 1. Cau truc log trong he thong

He thong tao 2 loai file theo ngay:
- `samples_YYYYMMDD.csv`: anh chup trang thai he thong theo chu ky (mac dinh 60 giay).
- `events_YYYYMMDD.csv`: su kien va quyet dinh quan trong (decision, warning, error, RPC, profile update...).

Quy uoc:
- Muc tieu cua `samples`: tra loi cau hoi "he thong dang o trang thai nao?".
- Muc tieu cua `events`: tra loi cau hoi "vi sao he thong lai hanh dong nhu vay?".

## 2. Cac cot can uu tien xem

### 2.1. Trong samples

Nhom cot chinh:
- Thoi gian: `ts_ms`, `iso_time`, `rtc_unix`.
- Cam bien: `temperature`, `hum`, `ldr`, `soil`.
- Chap hanh: `motor_speed`, `usb1_state`, `usb2_state`, `led_brightness`.
- Ket noi: `wifi_connected`, `wifi_ip`, `rssi`.
- Thoi tiet rut gon: cac truong `om_*`, `aqi_*`, `api_*` can thiet.

Y nghia nhanh:
- `soil` giam keo dai + `usb2_state` it bat: can kiem tra nguong tuoi/profile.
- `motor_speed` tang cao lien tuc: moi truong co xu huong nong/am theo quy tac quat.
- `wifi_connected = false`: cloud telemetry co the thieu, nhung local control van phai tiep tuc.

### 2.2. Trong events

Nhom cot chinh:
- Thoi gian: `ts_ms`, `iso_time`.
- Ngu canh su kien: `kind`, `src`, `act`, `val`.
- Chi tiet bo sung: `meta_json`.

Y nghia nhanh:
- `kind=ai_local` hoac nhom decision: su kien quyet dinh dieu khien theo luat.
- `kind=warning/error`: can doi chieu voi samples cung khoang thoi gian.
- Su kien profile/RPC: dung de xac nhan thay doi cau hinh tu cloud da ap dung dung chua.

## 3. Quy trinh doc log de phan tich

Buoc 1: Xac dinh khoang thoi gian can xem
- Chon ngay/khung gio xuat hien hien tuong bat thuong hoac can danh gia.

Buoc 2: Doc file samples truoc
- Kiem tra xu huong cam bien/chap hanh theo thoi gian.
- Danh dau moc thoi gian co bien dong lon (soil roi nhanh, quat len max, den bat bat thuong...).

Buoc 3: Doi chieu sang events
- Tim su kien cung moc thoi gian de xac dinh nguyen nhan: decision, warning, manual override, RPC.

Buoc 4: Ket luan theo cap "trang thai - nguyen nhan"
- Trang thai (tu samples) + Nguyen nhan (tu events) => ket luan van hanh.

## 4. Mau dien giai cho bao cao

Mau 1 - Tuoi nuoc:
- Quan sat: `soil` giam tu 42 xuong 36 trong 20 phut, sau do `usb2_state=1` xuat hien theo chu ky ngan.
- Doi chieu event: co su kien decision nhom tuoi tai cung thoi diem.
- Dien giai: bo dieu khien kich hoat nhanh tuoi theo nguong dat, he thong dap ung dung theo profile.

Mau 2 - Dieu toc quat:
- Quan sat: `temperature` va/hoac VPD tang nhe, `motor_speed` tang tu 20 len 45.
- Doi chieu event: co su kien fan decision, khong co error.
- Dien giai: quat duoc dieu toc theo luat, khong dao dong bat/tat lien tuc.

Mau 3 - Mat mang tam thoi:
- Quan sat: `wifi_connected=false`, telemetry cloud gap.
- Doi chieu event: co su kien wifi watchdog/reconnect.
- Dien giai: he thong van duy tri local control, khong ngung dieu khien.

## 5. Danh gia chat luong du lieu log

Kiem tra nhanh moi ngay:
- Co du ca 2 file `samples_...` va `events_...`.
- Cot thoi gian tang don dieu, khong vo dong nghiem trong.
- Khong co doan mat du lieu dai bat thuong.
- So su kien warning/error o muc chap nhan duoc.

Neu phat hien bat thuong:
- Doi chieu nguon dien, wifi, profile active, va chu ky lay mau.
- Kiem tra lai cau hinh `SOIL_DRY/SOIL_WET` va nguong profile truoc khi ket luan loi dieu khien.

## 6. Goi y dung log cho so sanh baseline/optimized

Khi so sanh 2 giai doan:
- Su dung cung mot bo chi so tong hop (stability, VPD, stress, energy).
- Bao dam so ngay va khung gio so sanh co tinh tuong dong.
- Luon trich dan ca bang so lieu va dien giai event de tranh ket luan 1 chieu tu du lieu mau.

## 7. Tai lieu lien quan trong repo

- `chapters/chapter4.tex`: mo ta co che logging trong bao cao.
- `chapters/chapter5.tex`: phan tich ket qua dua tren log 19 ngay.
- `logs/`: thu muc du lieu goc theo ngay (samples/events).

Tai lieu nay duoc cap nhat de phuc vu van hanh, nghiem thu va bao ve khoa luan.