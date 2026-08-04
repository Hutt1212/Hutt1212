# Hướng dẫn đưa profile lên GitHub

## 1. Tạo repo đặc biệt
GitHub chỉ hiển thị README trên trang cá nhân khi repo **trùng tên với username** và ở chế độ **Public**:

- Vào https://github.com/new
- Repository name: `Hutt1212` (đúng chính xác)
- Chọn **Public** → Create repository (không cần tick tạo README)

## 2. Push code từ folder này

```bash
cd d:/Cong_viec/Hutt1212
git init
git add .
git commit -m "profile: new portfolio README"
git branch -M main
git remote add origin https://github.com/Hutt1212/Hutt1212.git
git push -u origin main
```

## 3. Lưu ý

- **File SVG trong `assets/profile/`** là phần tạo hiệu ứng riêng (thẻ dossier, toolkit, principles) — phải push cùng repo thì README mới hiển thị được, vì README tham chiếu đường dẫn tương đối `./assets/profile/...`.
- Muốn sửa nội dung thẻ (tên, dòng chữ, màu) → sửa trực tiếp file `.svg` bằng text editor.
- Badge **EMAIL** đang để `nguyenminhhuy01234@gmail.com` công khai — nếu ngại lộ email thì xóa 2 badge email trong README.
- Widget stats (`count_private=true`) chỉ đếm private commit khi bạn bật: GitHub → Settings → Profile → **Include private contributions on my profile**.
- File `HUONG-DAN.md` này không cần push (có thể xóa hoặc thêm vào `.gitignore`).
