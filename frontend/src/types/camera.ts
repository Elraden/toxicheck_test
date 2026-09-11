export type ScanMode = 'barcode' | 'text';

export type ScannerStatus =
  | 'idle'
  | 'starting'
  | 'scanning'
  | 'loading'
  | 'done'
  | 'error';

export type NumericCameraCapability = {
  min: number;
  max: number;
  step?: number;
};

export type ExtendedCameraCapabilities =
  MediaTrackCapabilities & {
    zoom?: NumericCameraCapability;
    torch?: boolean;
    focusMode?: string[];
    focusDistance?: NumericCameraCapability;
    exposureCompensation?: NumericCameraCapability;
  };

export type ExtendedCameraSettings =
  MediaTrackSettings & {
    zoom?: number;
    torch?: boolean;
    focusMode?: string;
  };

export type ExtendedCameraConstraints =
  MediaTrackConstraintSet & {
    zoom?: number;
    torch?: boolean;
    focusMode?: string;
  };
