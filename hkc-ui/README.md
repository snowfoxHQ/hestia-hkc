# HKC 知识星球前端（3D）

中栏的知识图谱是基于 Three.js (WebGL) 的 3D 知识星球：节点是浮于深空的
3D 球体，靠语义引力聚拢、缓缓漂浮；可拖拽转视角、滚轮缩放、点击节点飞入并
在右栏查看词条详情。

## 如何打开

3D 版本使用 ES Module 加载 Three.js，**必须通过 http 访问**，不能直接双击
本地 HTML 文件（浏览器 CORS 策略会阻止 file:// 下的模块加载）。

```bash
cd hkc-ui
python -m http.server 8080
# 浏览器打开 http://localhost:8080/index.html
```

或配合 hkc-api 后端使用时，通过后端服务地址访问。

## 交互

- 拖拽：旋转视角  · 滚轮：缩放  · 悬停节点：平滑放大
- 点击节点：相机飞入 + 右栏词条详情  · 点击空白：清除选中
- 适应视图按钮（中栏标题右上）：重置视角

## 依赖

vendor/three.module.min.js + vendor/three.core.min.js（Three.js r185，本地离线，无需网络）
