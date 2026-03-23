"""
HeadPose.py
Head pose estimator using compact 3D model + arcsin/arctan2 decomposition.
 
Instantiate once per session (camera matrix is computed once from frame size).
Call estimate(landmarks) every frame.
 
Returns: pitch, yaw, roll, rot_vec, trans_vec
    - pitch, yaw, roll : raw angles in degrees (no baseline subtraction — caller handles that)
    - rot_vec, trans_vec : for nose arrow overlay 
    - Returns (None, None, None, None, None) if solvePnP fails
"""

import cv2
import numpy as np

# Landmark indices used for solvePnP (MediaPipe FaceMesh)
# Nose tip, chin, right eye corner, left eye corner, right mouth corner, left mouth corner
_FACE_2D_INDICES = [1, 152, 263, 33, 291, 61]

# Compact 3D face model points corresponding to the above 2D landmarks
_FACE_3D_POINTS = np.array([
    [0.0,    0.0,    0.0   ],   # Nose tip
    [0.0,   -63.6,  -12.5  ],   # Chin
    [-43.3,  32.7,  -26.0  ],   # Right eye corner
    [43.3,   32.7,  -26.0  ],   # Left eye corner
    [-28.9, -28.9,  -24.1  ],   # Right mouth corner
    [28.9,  -28.9,  -24.1  ],   # Left mouth corner
], dtype=np.float64)


class HeadPose:
    def __init__(self, img_width, img_height):
        self._img_w = img_width
        self._img_h = img_height
        
        # Camera internals
        f = float(self._img_w)  # Approximate focal length
        center = (self._img_w / 2, self._img_h / 2)
        self._camera_matrix = np.array([
            [f, 0, center[0]],
            [0, f, center[1]],
            [0, 0, 1]
        ], dtype=np.float64)
        self._dist_coeffs = np.zeros((4, 1), dtype=np.float64)
        
    def estimate(self, landmarks):
        face_2d = np.array([
            [landmarks[i].x * self._img_w, landmarks[i].y * self._img_h]
            for i in _FACE_2D_INDICES
        ], dtype=np.float64)
        
        success, rot_vec, trans_vec = cv2.solvePnP(
            _FACE_3D_POINTS, face_2d, self._camera_matrix, self._dist_coeffs,
            flags=cv2.SOLVEPNP_ITERATIVE
        )
        
        if not success:
            return None, None, None, None, None
        
        rot_mat, _ = cv2.Rodrigues(rot_vec)
        pitch = round(np.degrees(np.arcsin(-rot_mat[2, 0])), 1)
        yaw = round(np.degrees(np.arctan2(rot_mat[2, 1], rot_mat[2, 2])), 1)
        roll = round(np.degrees(np.arctan2(rot_mat[1, 0], rot_mat[0, 0])), 1)

        return pitch, yaw, roll, rot_vec, trans_vec