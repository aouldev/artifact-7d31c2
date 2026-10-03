const Module = require('module');

const originalLoad = Module._load;
const canvasMock = {
  Canvas: class Canvas {},
  CanvasRenderingContext2D: class CanvasRenderingContext2D {},
  Image: class Image {},
  ImageData: class ImageData {},
  DOMMatrix: class DOMMatrix {},
  DOMPoint: class DOMPoint {},
  createCanvas: () => ({
    getContext: () => ({}),
    toBuffer: () => Buffer.alloc(0),
    toDataURL: () => 'data:,',
  }),
  createImageData: () => ({}),
  loadImage: async () => ({}),
  registerFont: () => {},
};

Module._load = function patchedLoad(request, parent, isMain) {
  if (request === 'canvas' || request.endsWith('/canvas') || request.endsWith('/canvas.node')) {
    return canvasMock;
  }
  try {
    return originalLoad.call(this, request, parent, isMain);
  } catch (error) {
    if (
      error &&
      error.code === 'MODULE_NOT_FOUND' &&
      typeof error.message === 'string' &&
      error.message.includes("../build/Release/canvas.node")
    ) {
      return canvasMock;
    }
    throw error;
  }
};
