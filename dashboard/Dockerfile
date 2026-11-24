# Build assets without embedding runtime credentials.
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Same proxy for local and Compose. It runs without root or added capabilities.
FROM python:3.12-alpine
WORKDIR /app
COPY --from=build /app/dist /app/dist
COPY secure_proxy.py /app/secure_proxy.py
USER 10001:10001
EXPOSE 8080
CMD ["python", "-u", "/app/secure_proxy.py"]
