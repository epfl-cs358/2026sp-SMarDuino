"""
camera_diagnostic.py
====================
Run this BEFORE pnp_bottom_vision.py to find out exactly what
the camera sees and where detection breaks.

Shows 4 windows:
  1. Raw camera feed
  2. Grayscale (brightness only)
  3. Threshold mask (adjust trackbar until your object is white)
  4. Contours found (green = valid size, red = too small/large)

Also prints contour info to the terminal so you can see areas and positions.

Run:
    python camera_diagnostic.py

Controls:
    Trackbar  -> adjust threshold (0 = Otsu auto)
    i         -> invert threshold
    q         -> quit and print the threshold value to use in pnp_bottom_vision.py
"""

import cv2
import numpy as np

CAMERA_INDEX = 1        # change to 1 or 2 if wrong camera
FRAME_WIDTH  = 1280
FRAME_HEIGHT = 720
CENTER_RADIUS = 280     # grey circle in the main view

def main():
    # CAP_DSHOW is required on Windows for reliable USB camera access
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_AUTOFOCUS,    0)    # disable autofocus
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)   # manual exposure
    cap.set(cv2.CAP_PROP_EXPOSURE,    -6)    # raise to -4 if image is too dark
    cap.set(cv2.CAP_PROP_BRIGHTNESS,  150)   # compensate for shorter exposure

    if not cap.isOpened():
        print(f"[ERROR] Cannot open camera {CAMERA_INDEX}")
        print("        Try CAMERA_INDEX = 1 or 2")
        return

    print("Camera opened OK")
    print("Adjust the trackbar until your component appears WHITE on black background")
    print("Press 'i' to invert if everything is white, 'q' to quit")

    WIN_RAW      = "1 - Raw camera"
    WIN_GRAY     = "2 - Grayscale"
    WIN_THRESH   = "3 - Threshold (adjust me)"
    WIN_CONTOURS = "4 - Contours found"

    cv2.namedWindow(WIN_THRESH)
    cv2.createTrackbar("Threshold (0=Otsu)", WIN_THRESH, 0, 255, lambda x: None)

    invert = False
    frame_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[ERROR] No frame")
            break

        # ── Grayscale (Value channel from HSV)
        hsv  = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        v    = hsv[:, :, 2]
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        v_eq = clahe.apply(v)
        blur = cv2.GaussianBlur(v_eq, (9, 9), 0)

        # ── Threshold
        cx0, cy0 = FRAME_WIDTH // 2, FRAME_HEIGHT // 2
        t = cv2.getTrackbarPos("Threshold (0=Otsu)", WIN_THRESH)
        if t == 0:
            # Compute Otsu from center-region pixels only so peripheral
            # background doesn't skew the histogram into a giant blob.
            roi_circle = np.zeros(blur.shape, dtype=np.uint8)
            cv2.circle(roi_circle, (cx0, cy0), CENTER_RADIUS, 255, -1)
            center_pixels = blur[roi_circle > 0].reshape(-1, 1)
            otsu_val, _ = cv2.threshold(center_pixels, 0, 255,
                                        cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            _, thresh = cv2.threshold(blur, int(otsu_val), 255, cv2.THRESH_BINARY)
            thresh_label = f"Otsu auto (val={otsu_val:.0f})"
        else:
            _, thresh = cv2.threshold(blur, t, 255, cv2.THRESH_BINARY)
            thresh_label = f"Manual thresh={t}"

        if invert:
            thresh = cv2.bitwise_not(thresh)
            thresh_label += " [INVERTED]"

        # Morphology cleanup
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        clean  = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=3)
        clean  = cv2.morphologyEx(clean,  cv2.MORPH_OPEN,  kernel, iterations=2)

        # Clip to the nozzle search zone, then auto-invert so the component
        # is always white — mirrors the engine's polarity logic so contours
        # trace the IC body itself rather than the surrounding background.
        roi_clip = np.zeros(blur.shape, dtype=np.uint8)
        cv2.circle(roi_clip, (cx0, cy0), CENTER_RADIUS, 255, -1)
        clean_clipped = cv2.bitwise_and(clean, roi_clip)
        zone_total = int(np.count_nonzero(roi_clip))
        zone_white_raw = int(np.count_nonzero(clean_clipped))
        if zone_total > 0 and zone_white_raw / zone_total > 0.60:
            clean_clipped = cv2.bitwise_not(clean_clipped)
            clean_clipped = cv2.bitwise_and(clean_clipped, roi_clip)

        # ── Contours
        contours, _ = cv2.findContours(clean_clipped, cv2.RETR_EXTERNAL,
                                        cv2.CHAIN_APPROX_SIMPLE)

        contour_vis = frame.copy()
        cv2.circle(contour_vis, (cx0, cy0), CENTER_RADIUS, (80, 80, 80), 1)
        cv2.line(contour_vis, (cx0-30, cy0), (cx0+30, cy0), (80,80,80), 1)
        cv2.line(contour_vis, (cx0, cy0-30), (cx0, cy0+30), (80,80,80), 1)

        valid_found = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            M    = cv2.moments(cnt)
            if M["m00"] == 0:
                continue
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])
            dist = float(np.hypot(cx - cx0, cy - cy0))

            if area < 500:
                # Too small — draw tiny red dot
                cv2.circle(contour_vis, (cx, cy), 3, (0, 0, 180), -1)
            elif area > 250_000:
                # Too large — draw red outline
                cv2.drawContours(contour_vis, [cnt], -1, (0, 0, 255), 2)
                cv2.putText(contour_vis, f"TOO LARGE {area:.0f}",
                            (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,255), 1)
            elif dist > CENTER_RADIUS:
                # Valid size but outside nozzle zone — yellow
                cv2.drawContours(contour_vis, [cnt], -1, (0, 200, 255), 2)
                cv2.putText(contour_vis, f"outside zone",
                            (cx-40, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0,200,255), 1)
            else:
                # Valid! — green
                cv2.drawContours(contour_vis, [cnt], -1, (0, 255, 60), 2)
                cv2.putText(contour_vis, f"OK area={area:.0f}",
                            (cx-40, cy-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,60), 1)
                valid_found.append((area, cx, cy, dist))

        # Stats: show raw (pre-invert) background coverage so the user can
        # judge whether the threshold is separating component from background
        white_pct = 100 * zone_white_raw / max(zone_total, 1)
        thresh_rgb = cv2.cvtColor(clean, cv2.COLOR_GRAY2BGR)
        cv2.putText(thresh_rgb, thresh_label,
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0,200,255), 2)
        cv2.putText(thresh_rgb, f"White: {white_pct:.1f}%  (>60% = wrong direction -> press i)",
                    (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0,0,255) if white_pct > 60 else (0,255,60), 1)
        cv2.putText(thresh_rgb, f"Valid contours in zone: {len(valid_found)}",
                    (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                    (0,255,60) if valid_found else (0,0,255), 2)

        # Print to terminal every 60 frames
        frame_count += 1
        if frame_count % 60 == 0:
            print(f"\n[Frame {frame_count}]  White%={white_pct:.1f}  "
                  f"Valid contours={len(valid_found)}  Thresh={t}  Invert={invert}")
            for i, (a, cx, cy, d) in enumerate(valid_found):
                print(f"  Contour {i}: area={a:.0f}  centre=({cx},{cy})  dist_from_centre={d:.0f}px")
            if not valid_found and contours:
                print(f"  {len(contours)} contours found but none passed filters:")
                for cnt in contours[:5]:
                    a  = cv2.contourArea(cnt)
                    M  = cv2.moments(cnt)
                    if M["m00"] == 0: continue
                    cx = int(M["m10"]/M["m00"])
                    cy = int(M["m01"]/M["m00"])
                    d  = float(np.hypot(cx-cx0, cy-cy0))
                    print(f"    area={a:.0f}  centre=({cx},{cy})  dist={d:.0f}  "
                          f"{'too small' if a<500 else 'too large' if a>250000 else 'outside zone'}")

        # Show all windows
        cv2.imshow(WIN_RAW,      frame)
        cv2.imshow(WIN_GRAY,     blur)
        cv2.imshow(WIN_THRESH,   thresh_rgb)
        cv2.imshow(WIN_CONTOURS, contour_vis)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif key == ord("i"):
            invert = not invert
            print(f"[Invert] {'ON' if invert else 'OFF'}")

    cap.release()
    cv2.destroyAllWindows()

    # Final instructions
    t_final = cv2.getTrackbarPos("Threshold (0=Otsu)", WIN_THRESH) if False else t
    print("\n" + "="*50)
    print("  DIAGNOSTIC COMPLETE")
    print("="*50)
    print(f"  Best threshold value found : {t_final}")
    print(f"  Invert was                 : {invert}")
    print()
    print("  In pnp_bottom_vision.py set these at the top:")
    print(f"    -> Use trackbar value in the debug window")
    print("="*50)


if __name__ == "__main__":
    main()
