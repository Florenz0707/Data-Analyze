FROM node:20.20.2-slim AS builder

WORKDIR /app

COPY frontend/vue_frontend/package.json frontend/vue_frontend/package-lock.json ./
RUN npm ci

COPY frontend/vue_frontend ./
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginx:1.29-alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY deploy/nginx/default.conf.template /etc/nginx/templates/default.conf.template

CMD ["nginx", "-g", "daemon off;"]
