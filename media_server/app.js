const NodeMediaServer = require('node-media-server');

const config = {
  rtmp: {
    port: 19350,
    chunk_size: 60000,
    gop_cache: true,
    ping: 30,
    ping_timeout: 60
  },
  http: {
    port: 8001,
    mediaroot: './media',
    allow_origin: '*'
  },
  auth: {
    api: true,
    api_user: 'admin',
    api_pass: '123456'
  },
  relay: {
    ffmpeg: 'D:/Vue/platform-yaokong-main/ffmpeg-8.0.1-essentials_build/bin/ffmpeg.exe',
    tasks: []
  }
};

var nms = new NodeMediaServer(config);
nms.run();

console.log('Node Media Server is running with API enabled.');
console.log('Ready to receive commands from the main backend.');
