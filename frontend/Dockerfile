# syntax=docker/dockerfile:1

# ---- builder: install dependencies and produce the static bundle ----
FROM node:22.11-alpine AS builder

WORKDIR /app

# package.json before source: this layer only rebuilds when dependencies actually change.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# ---- runtime: nginx only. No Node, no node_modules, no source ----
FROM nginx:1.27-alpine

# Run as the image's built-in unprivileged user rather than root. A port below 1024 needs root
# to bind, so nginx listens on 8080 here (see nginx.conf) instead of the default 80.
RUN sed -i '/^user /d' /etc/nginx/nginx.conf \
    && touch /var/run/nginx.pid \
    && chown -R nginx:nginx /var/cache/nginx /var/run/nginx.pid /usr/share/nginx/html

COPY --from=builder --chown=nginx:nginx /app/dist /usr/share/nginx/html
COPY --chown=nginx:nginx nginx.conf /etc/nginx/conf.d/default.conf

USER nginx

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/ || exit 1

# Exec form: nginx is PID 1 and receives SIGTERM directly.
CMD ["nginx", "-g", "daemon off;"]
